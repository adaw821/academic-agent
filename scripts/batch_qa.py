"""Ask independent Markdown questions against an existing or newly uploaded corpus.

Run with the project's agent/Scripts/python.exe; requires httpx (already installed).
No reference answers are submitted. Credentials are never written to result files.
"""
import argparse
from datetime import datetime, timezone
import getpass
import hashlib
import json
import math
from pathlib import Path
import re
import subprocess
import sys
import time
from uuid import uuid4

import httpx

ROOT = Path(__file__).resolve().parents[1]
TYPES = {".pdf": "application/pdf", ".txt": "text/plain",
         ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
         ".pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation"}


def read_questions(path):
    """Read inline headings or frozen Q blocks; references remain local metadata."""
    questions = []
    seen = set()
    text = path.read_text(encoding="utf-8-sig")
    blocks = re.split(r"(?m)^#{2,4}\s+(Q\d+)\b([^\n]*)\n?", text)
    for offset in range(1, len(blocks), 3):
        qid, heading, content = blocks[offset:offset + 3]
        inline = re.search(r"问题[：:]\s*(.+)", heading)
        question = re.search(r"(?m)^\*\*问题\*\*[：:]\s*(.+)$", content)
        if inline is None and question is None:
            raise ValueError(f"题目缺少问题正文：{qid}")
        query = (inline or question).group(1).strip()
        if qid in seen or not 1 <= len(query) <= 10000:
            raise ValueError(f"题目重复或长度不合法：{qid}")
        seen.add(qid)
        entry = {"id": qid, "query": query}
        reference = re.search(r"(?m)^\*\*参考答案\*\*[：:]\s*(.+)$", content)
        if reference:
            entry["reference"] = reference.group(1).strip()
        questions.append(entry)
    if not questions:
        raise ValueError("未找到题目；支持 #### Q01 ... 问题：... 或 ## Q01 标题后跟 **问题**：... 格式")
    return questions


def discover_files(directory):
    if not directory.is_dir():
        raise ValueError(f"测试文件夹不存在：{directory}")
    files = sorted((p for p in directory.iterdir() if p.is_file()
                    and p.suffix.lower() in TYPES and not p.name.startswith("~$")),
                   key=lambda p: p.name.casefold())
    if not files or any(p.stat().st_size == 0 for p in files):
        raise ValueError("没有支持的测试文件，或存在空文件")
    return files


def digest(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def save_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def decode_response(response):
    try:
        return response.json()
    except ValueError:
        return {"raw_text": response.text}


class API:
    def __init__(self, client, account, password):
        self.client, self.account, self.password = client, account, password
        self.login_at = None

    def login(self):
        response = self.client.post("/api/user/login", json={
            "account": self.account, "password": self.password,
        })
        if response.status_code != 200 or not self.client.cookies.get("session_id"):
            raise RuntimeError(f"登录失败（HTTP {response.status_code}），请检查账号和密码")
        self.login_at = time.monotonic()

    def request(self, method, path, **kwargs):
        # Sessions expire after 30 minutes; refresh before long corpus/test runs.
        if self.login_at is None or time.monotonic() - self.login_at > 20 * 60:
            self.login()
        response = self.client.request(method, path, **kwargs)
        if response.status_code == 401:
            self.login()
            # Only retry an explicit authentication rejection, never timeouts/5xx.
            for _, (_, stream, _) in kwargs.get("files", []):
                stream.seek(0)
            response = self.client.request(method, path, **kwargs)
        return response

    def documents(self, project_id):
        response = self.request("GET", f"/api/projects/{project_id}/documents")
        if response.status_code != 200:
            raise RuntimeError(f"无法访问项目 {project_id}（HTTP {response.status_code}），确认项目属于登录账号")
        data = response.json()
        if not isinstance(data, list):
            raise RuntimeError("文档列表响应格式不正确")
        return data


def upload_corpus(api, project_id, files, run_dir, reuse_existing=False):
    existing = api.documents(project_id)
    names = {p.name for p in files}
    extras = [d["filename"] for d in existing if d["filename"] not in names]
    if extras:
        raise RuntimeError(f"项目中有本次语料之外的文档，可能影响结果：{extras}。请使用干净项目")
    if existing and not reuse_existing:
        raise RuntimeError("项目已有同名文档。避免重复上传已停止；确认内容一致后可加 --reuse-existing（只按文件名匹配）")
    mapping = []
    for index, path in enumerate(files, 1):
        matches = [d for d in existing if d["filename"] == path.name]
        if len(matches) > 1:
            raise RuntimeError(f"项目里有多个同名文档，无法确定复用对象：{path.name}")
        entry = {"filename": path.name, "local_path": str(path.resolve()),
                 "bytes": path.stat().st_size, "sha256": digest(path)}
        if matches:
            entry.update(document_id=matches[0]["id"], reused=True)
            print(f"复用：{path.name} → 文档 {entry['document_id']}", flush=True)
        else:
            print(f"上传 [{index}/{len(files)}] {path.name}", flush=True)
            with path.open("rb") as stream:
                response = api.request("POST", f"/api/projects/{project_id}/documents",
                                       files=[("files", (path.name, stream, TYPES[path.suffix.lower()]))])
            payload = decode_response(response)
            save_json(run_dir / f"upload_{index:02d}.json", {"http_status": response.status_code, "response": payload})
            saved = payload.get("saved_files", []) if isinstance(payload, dict) else []
            if response.status_code != 202 or len(saved) != 1 or payload.get("failed_files"):
                raise RuntimeError(f"上传失败：{path.name}，请查看 upload_{index:02d}.json")
            if saved[0]["filename"] != path.name:
                raise RuntimeError("上传响应中的文件名不匹配")
            entry.update(document_id=saved[0]["id"], reused=False)
        mapping.append(entry)
        save_json(run_dir / "documents.json", mapping)
    return mapping


def wait_for_documents(api, project_id, mapping, timeout, interval):
    deadline = time.monotonic() + timeout
    last = None
    ids = {d["document_id"] for d in mapping}
    while True:
        all_docs = api.documents(project_id)
        if {d["id"] for d in all_docs} - ids:
            raise RuntimeError("项目里出现本次语料之外的文档，停止提问以避免混入其他资料")
        current = {d["id"]: d["status"] for d in all_docs if d["id"] in ids}
        if current != last:
            print("解析状态：" + ", ".join(f"{d['filename']}={current.get(d['document_id'], 'missing')}" for d in mapping), flush=True)
            last = current
        if set(current) != ids:
            raise RuntimeError("测试文档被删除或列表缺失，停止提问")
        if any(status == "failed" for status in current.values()):
            raise RuntimeError("有文档解析失败；本轮未开始提问，请检查后端日志")
        if all(status == "completed" for status in current.values()):
            return
        if time.monotonic() >= deadline:
            raise RuntimeError("等待文档解析超时；未开始提问，可用 --reuse-existing 重新等待")
        time.sleep(min(interval, max(0, deadline - time.monotonic())))


def ask_questions(api, project_id, questions, percentage, run_dir):
    results = []
    for index, question in enumerate(questions, 1):
        # Each question is independent, and all project documents remain searchable.
        body = {"query": question["query"], "candidate_k_percentage": percentage,
                "conversation_id": None}
        record = {"question_id": question["id"], "request": body,
                  "started_at": datetime.now(timezone.utc).isoformat()}
        if "reference" in question:
            record["reference"] = question["reference"]
        print(f"提问 [{index}/{len(questions)}] {question['id']} {question['query']}", flush=True)
        started = time.perf_counter()
        auth_failed = False
        try:
            response = api.request("POST", f"/api/projects/{project_id}/ask", json=body)
            record.update(http_status=response.status_code, response=decode_response(response))
            payload = record["response"]
            record["request_ok"] = (response.status_code == 200 and isinstance(payload, dict)
                                    and isinstance(payload.get("answer"), str)
                                    and isinstance(payload.get("sources"), list)
                                    and isinstance(payload.get("retrieved_contexts"), list))
        except httpx.RequestError as exc:
            record.update(http_status=None, request_ok=False,
                          error=f"{type(exc).__name__}: {exc}",
                          note="请求结果不确定，服务端可能已处理；未自动重试")
        except RuntimeError as exc:
            record.update(http_status=None, request_ok=False, error=str(exc), note="登录续期失败，停止后续问题")
            auth_failed = True
        record["elapsed_seconds"] = round(time.perf_counter() - started, 3)
        results.append(record)
        save_json(run_dir / f"{question['id']}.json", record)
        with (run_dir / "results.jsonl").open("a", encoding="utf-8") as output:
            output.write(json.dumps(record, ensure_ascii=False) + "\n")
        if record["request_ok"]:
            payload = record["response"]
            sample = {"user_input": question["query"], "response": payload["answer"],
                      "retrieved_contexts": payload["retrieved_contexts"]}
            if "reference" in question:
                sample["reference"] = question["reference"]
            with (run_dir / "ragas.jsonl").open("a", encoding="utf-8") as output:
                output.write(json.dumps(sample, ensure_ascii=False) + "\n")
        write_report(run_dir, results)
        print(f"  HTTP {record['http_status']}，耗时 {record['elapsed_seconds']} 秒", flush=True)
        if auth_failed or record["http_status"] in (401, 403, 404):
            raise RuntimeError("登录、权限或项目异常，已保存当前结果并停止后续问题")
    return results


def write_report(run_dir, results):
    success = sum(r["request_ok"] for r in results)
    lines = ["# 批量问答实测记录", "", f"已记录 {len(results)} 题，接口成功 {success} 题。",
             "接口成功不等于答案正确；答案质量、拒答和检索噪声需人工核对。",
             "每题独立会话；检索整个项目，不根据题集的目标文档限定范围。",
             "首题可能包含模型首次加载耗时；原题集的旧结论不作为本轮结果。", ""]
    for record in results:
        lines += [f"## {record['question_id']}", "", record["request"]["query"], "",
                  f"HTTP：{record['http_status']}；耗时：{record['elapsed_seconds']} 秒", ""]
        payload = record.get("response", {})
        if isinstance(payload, dict):
            answer = payload.get("answer")
            lines += [answer if isinstance(answer, str) else json.dumps(payload, ensure_ascii=False), "",
                      "来源：`" + json.dumps(payload.get("sources", []), ensure_ascii=False) + "`", ""]
        if record.get("error"):
            lines += [record["error"], record["note"], ""]
        if "reference" in record:
            lines += ["参考答案（仅本地核对）：" + record["reference"], ""]
        lines += ["人工判定：待核对", ""]
    (run_dir / "report.md").write_text("\n".join(lines), encoding="utf-8")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--questions", type=Path, required=True)
    corpus = parser.add_mutually_exclusive_group(required=True)
    corpus.add_argument("--files-dir", type=Path)
    corpus.add_argument("--skip-upload", action="store_true", help="直接提问已有项目，要求所有文档处理完成")
    parser.add_argument("--project-id", type=int, default=1)
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--account", help="登录邮箱或手机号；不填则运行时询问")
    parser.add_argument("--credentials-file", type=Path, help="本地 JSON 登录配置，包含 account/password")
    parser.add_argument("--candidate-percentage", type=float, default=0.4)
    parser.add_argument("--request-timeout", type=float, default=600)
    parser.add_argument("--parse-timeout", type=float, default=7200)
    parser.add_argument("--poll-interval", type=float, default=10)
    parser.add_argument("--expected-count", type=int, default=20)
    parser.add_argument("--output-root", type=Path, default=ROOT / "test_files" / "batch_runs")
    parser.add_argument("--reuse-existing", action="store_true")
    parser.add_argument("--dry-run", action="store_true", help="只检查题目和文件，不登录、不上传、不提问")
    args = parser.parse_args()
    if args.project_id < 1 or not 0 < args.candidate_percentage <= 1:
        parser.error("项目 ID 必须为正整数，召回比例必须在 (0, 1] 内")
    if any(not math.isfinite(v) or v <= 0 for v in (args.request_timeout, args.parse_timeout, args.poll_interval)):
        parser.error("超时和轮询间隔必须为有限正数")
    questions = read_questions(args.questions)
    if len(questions) != args.expected_count:
        raise ValueError(f"题目数量为 {len(questions)}，预期 {args.expected_count}，请核对题集")
    files = discover_files(args.files_dir) if args.files_dir else []
    corpus_label = "使用项目现有文档" if args.skip_upload else f"{len(files)} 个文件"
    print(f"项目 {args.project_id}；{corpus_label}；{len(questions)} 道独立问题；召回比例 {args.candidate_percentage}")
    for path in files:
        print(f"  文件：{path.name} ({path.stat().st_size / 1024 / 1024:.1f} MiB)")
    if args.dry_run:
        for question in questions:
            print(f"  {question['id']}: {question['query']}")
        return 0
    if args.credentials_file:
        try:
            credentials = json.loads(args.credentials_file.read_text(encoding="utf-8-sig"))
        except (OSError, ValueError):
            raise ValueError("无法读取登录配置，请检查文件路径和 JSON 格式") from None
        if not isinstance(credentials, dict) or any(
            not isinstance(credentials.get(key), str) or not credentials[key].strip()
            for key in ("account", "password")
        ):
            raise ValueError("登录配置必须包含非空字符串 account 和 password")
        account, password = credentials["account"].strip(), credentials["password"]
        if args.account and args.account != account:
            raise ValueError("--account 与登录配置账号不一致，请使用对应的登录配置")
    else:
        account = args.account or input("登录邮箱或手机号：").strip()
        password = getpass.getpass("登录密码（不回显、不保存）：")
    if not account or not password:
        raise ValueError("账号和密码不能为空")
    run_dir = args.output_root / (datetime.now().strftime("%Y%m%d_%H%M%S") + "_" + uuid4().hex[:6])
    run_dir.mkdir(parents=True)
    print(f"结果目录：{run_dir}", flush=True)
    commit = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True)
    metadata = {"project_id": args.project_id, "base_url": args.base_url,
                "corpus_mode": "existing_project" if args.skip_upload else "upload",
                "candidate_k_percentage": args.candidate_percentage, "conversation_mode": "independent",
                "questions_path": str(args.questions.resolve()), "questions_sha256": digest(args.questions),
                "client_workspace_commit": commit.stdout.strip(), "server_commit": "未核验",
                "status": "running", "expected_question_count": len(questions)}
    save_json(run_dir / "questions.json", questions)
    save_json(run_dir / "run.json", metadata)
    try:
        with httpx.Client(base_url=args.base_url.rstrip("/"), timeout=args.request_timeout,
                          follow_redirects=False, trust_env=False) as client:
            api = API(client, account, password)
            if args.skip_upload:
                mapping = existing_corpus(api, args.project_id)
                save_json(run_dir / "documents.json", mapping)
            else:
                mapping = upload_corpus(api, args.project_id, files, run_dir, args.reuse_existing)
            wait_for_documents(api, args.project_id, mapping, args.parse_timeout, args.poll_interval)
            results = ask_questions(api, args.project_id, questions, args.candidate_percentage, run_dir)
        metadata["status"] = "completed" if all(r["request_ok"] for r in results) else "completed_with_errors"
        print(f"完成：{sum(r['request_ok'] for r in results)}/{len(results)} 题接口成功；请查看 report.md", flush=True)
        return 0 if metadata["status"] == "completed" else 1
    except BaseException as exc:
        metadata.update(status="interrupted" if isinstance(exc, KeyboardInterrupt) else "failed",
                        error=f"{type(exc).__name__}: {exc}")
        raise
    finally:
        metadata["finished_at"] = datetime.now(timezone.utc).isoformat()
        save_json(run_dir / "run.json", metadata)


def existing_corpus(api, project_id):
    documents = api.documents(project_id)
    if not documents:
        raise RuntimeError("项目没有文档，请先上传测试资料")
    if any(doc["status"] != "completed" for doc in documents):
        raise RuntimeError("项目存在未完成或失败的文档，请处理完成后再提问")
    return [{"document_id": doc["id"], "filename": doc["filename"], "reused": True}
            for doc in documents]


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (ValueError, RuntimeError, OSError, httpx.HTTPError) as error:
        print(f"停止：{error}", file=sys.stderr)
        sys.exit(1)
