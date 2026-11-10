"""Run with the project Python: python tests/test_context_management.py.

Uses real services/ORM with temporary SQLite databases. Model calls, tokenizer
counts and retrieval are mocked; no production database or API is contacted.
"""

import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

with patch.dict(os.environ, {
    "DATABASE_URL": "sqlite:///:memory:",
    "ZHIPU_API_KEY": "unused-test-key",
    "HF_HUB_OFFLINE": "1",
}):
    from main import app
    from app.db.database import Base
    from app.models.users import User
    from app.models.projects import Project
    from app.models.conversation import Conversation
    from app.models.message import Message
    from app.models.documents import Document
    from app.routers import rag_flow
    from app.services import answer_services as answers
    from app.services import context_management as context
    from app.services import search_services as search
    from app.services import school_qa

from fastapi import HTTPException
from app.schemas.search import SearchRequest
from langchain_core.messages import AIMessage
from sqlalchemy import create_engine, select, update
from sqlalchemy.orm import sessionmaker


def model(content="answer", finish="stop"):
    result = Mock()
    result.bind.return_value = result
    result.invoke.return_value = AIMessage(content=content, response_metadata={"finish_reason": finish})
    return result


class ContextTests(unittest.TestCase):
    def setUp(self):
        translation = patch.object(rag_flow, "translator", model('{"translation": null}'))
        translation.start()
        self.addCleanup(translation.stop)
        self.temp = tempfile.TemporaryDirectory()
        self.engine = create_engine("sqlite:///" + (Path(self.temp.name) / "test.db").as_posix())
        Base.metadata.create_all(self.engine, tables=[
            User.__table__, Project.__table__, Conversation.__table__, Message.__table__,
            Document.__table__,
        ])
        self.sessions = sessionmaker(bind=self.engine, autoflush=False)
        self.db = self.sessions()
        self.db.add(User(id=1, username="tester", email="test@example.com", password_hash="unused"))
        self.db.add(Project(id=1, user_id=1, name="project"))
        self.db.add(Conversation(id=1, project_id=1))
        self.db.commit()
        self.chunk = [{"filename": "doc", "parent_content": "evidence", "document_id": 1, "parent_id": 1}]

    def tearDown(self):
        self.db.close()
        self.engine.dispose()
        self.temp.cleanup()

    def add_turns(self, first, last, legacy=False):
        for turn in range(first, last + 1):
            for role in ("user", "assistant"):
                self.db.add(Message(
                    conversation_id=1, role=role, content=f"{turn}-{role}",
                    turn_id=None if legacy else str(turn),
                ))
        self.db.commit()

    def read(self):
        return answers.get_history_messages(1, self.db, 1)

    def ask(self, counts, response_model=None):
        history, summary, boundary = self.read()
        response_model = response_model or model()
        with patch.object(answers, "count_message_tokens", side_effect=counts), patch.object(answers, "llm", response_model):
            return answers.generate_answer(
                "question", self.chunk, history, 1, self.db, 1,
                summary=summary, summary_boundary=boundary,
            )

    def compress(self, boundary, expected=None):
        return context.context_compress("new history", 1, self.db, 1, boundary, expected)

    def test_new_conversation_and_route_save(self):
        self.assertEqual(answers.get_history_messages(None, self.db, 1), ([], "", None))
        request = SearchRequest(query="question", conversation_id=None, candidate_k_percentage=0.4)
        with patch.object(search, "llm", model()) as rewrite, patch.object(answers, "llm", model()), \
             patch.object(answers, "count_message_tokens", return_value=100), \
             patch.object(school_qa, "plan_assignment_query", return_value=None), \
             patch.object(rag_flow, "_scope_has_materials", return_value=True), \
             patch.object(rag_flow, "needs_materials", return_value=True), \
             patch.object(rag_flow, "search_vector", return_value=[]), \
             patch.object(rag_flow, "search_bm25", return_value=[]), \
             patch.object(rag_flow, "rerank_results", return_value=self.chunk):
            with patch.object(rag_flow, "translate_query", return_value=None):
                result = rag_flow.execute_qa(1, request, self.db, self.db.get(User, 1))
        rewrite.invoke.assert_not_called()
        messages = self.db.scalars(select(Message).where(Message.conversation_id == result["conversation_id"]).order_by(Message.id)).all()
        self.assertEqual([m.role for m in messages], ["user", "assistant"])
        self.assertEqual(messages[0].turn_id, messages[1].turn_id)

    def test_incremental_summary_and_uncompressed_reuse(self):
        self.add_turns(1, 7)
        summary_model = model("summary-one")
        with patch.object(context, "llm", summary_model), patch.object(context, "count_message_tokens", return_value=100):
            self.ask([24000, 100])
            history, summary, boundary = self.read()
            self.assertEqual((summary, boundary), ("summary-one", 4))
            self.assertEqual([h["id"] for h in history], list(range(5, 15)))
            first_prompt = summary_model.invoke.call_args.args[0][1].content
            self.assertIn("1-user", first_prompt)
            self.assertIn("2-assistant", first_prompt)
            self.assertNotIn("3-user", first_prompt)
            answer_model = model()
            self.ask([100], answer_model)
            self.assertIn("summary-one", answer_model.invoke.call_args.args[0][1].content)
            self.assertEqual(summary_model.invoke.call_count, 1)
            self.add_turns(8, 8)
            summary_model.invoke.return_value = AIMessage(content="summary-two")
            self.ask([24000, 100])
        second_prompt = summary_model.invoke.call_args.args[0][1].content
        self.assertIn("summary-one", second_prompt)
        self.assertIn("3-user", second_prompt)
        self.assertNotIn("1-user", second_prompt)
        self.assertEqual(self.read()[1:], ("summary-two", 6))

    def test_untracked_summary_and_more_than_100_messages(self):
        self.add_turns(1, 55)
        conversation = self.db.get(Conversation, 1)
        conversation.summary = "untracked old summary"
        self.db.commit()
        history, summary, boundary = self.read()
        self.assertEqual(len(history), 110)
        self.assertEqual((summary, boundary), ("", None))

    def test_legacy_and_multi_message_turns_preserve_order(self):
        for legacy in (False, True):
            with self.subTest(legacy=legacy):
                self.db.query(Message).delete()
                self.db.commit()
                self.add_turns(1, 6, legacy)
                if not legacy:
                    self.db.add(Message(conversation_id=1, role="assistant", content="extra-reply", turn_id="6"))
                    self.db.commit()
                history, _, _ = self.read()
                answer_model = model()
                with patch.object(answers, "context_compress", return_value="summary") as compress:
                    self.ask([24000, 100], answer_model)
                self.assertEqual(compress.call_args.kwargs["summarized_until_id"], history[1]["id"])
                prompt = answer_model.invoke.call_args.args[0][1].content
                self.assertNotIn("1-user", prompt)
                positions = [prompt.index(h["content"]) for h in history[2:]]
                self.assertEqual(positions, sorted(positions))

    def test_no_older_turns_rejects_without_summary_call(self):
        self.add_turns(1, 5)
        with patch.object(answers, "context_compress") as compress:
            with self.assertRaises(HTTPException) as failure:
                self.ask([24000])
            self.assertEqual(failure.exception.status_code, 400)
            compress.assert_not_called()

    def test_interleaved_turns_do_not_skip_retained_messages(self):
        for index, turn in enumerate(["a", "b", "c", "d", "e", "f", "a"]):
            self.db.add(Message(conversation_id=1, role="user", content=str(index), turn_id=turn))
        self.db.commit()
        with patch.object(answers, "context_compress") as compress:
            with self.assertRaises(HTTPException):
                self.ask([24000])
            compress.assert_not_called()

    def test_concurrent_writer_wins_without_boundary_rollback(self):
        # Keep a strong reference to simulate an ORM snapshot read before the other writer.
        stale = self.db.get(Conversation, 1)
        stale.summary, stale.summarized_until_id = "old", 2
        self.db.commit()
        self.db.refresh(stale)
        def concurrent_update(_messages):
            with self.sessions() as other:
                other.execute(update(Conversation).where(Conversation.id == 1).values(summary="winner", summarized_until_id=8))
                other.commit()
            return AIMessage(content="stale result")
        summary_model = model()
        summary_model.invoke.side_effect = concurrent_update
        with patch.object(context, "llm", summary_model), patch.object(context, "count_message_tokens", return_value=100):
            with self.assertRaises(HTTPException) as failure:
                self.compress(6, expected=2)
        self.assertEqual(failure.exception.status_code, 409)
        self.db.expire_all()
        current = self.db.get(Conversation, 1)
        self.assertEqual((current.summary, current.summarized_until_id), ("winner", 8))

    def test_concurrent_first_summary_with_null_boundary(self):
        def concurrent_update(_messages):
            with self.sessions() as other:
                other.execute(update(Conversation).where(Conversation.id == 1).values(summary="winner", summarized_until_id=4))
                other.commit()
            return AIMessage(content="loser")
        summary_model = model()
        summary_model.invoke.side_effect = concurrent_update
        with patch.object(context, "llm", summary_model), patch.object(context, "count_message_tokens", return_value=100):
            with self.assertRaises(HTTPException) as failure:
                self.compress(4)
        self.assertEqual(failure.exception.status_code, 409)
        self.assertEqual(self.db.get(Conversation, 1).summary, "winner")

    def test_stale_snapshot_rejected_before_model_and_no_rewind(self):
        conversation = self.db.get(Conversation, 1)
        conversation.summary, conversation.summarized_until_id = "current", 8
        self.db.commit()
        with patch.object(context, "llm", model()) as summary_model:
            for boundary, expected in [(10, 2), (8, 8), (6, 8)]:
                with self.subTest(boundary=boundary, expected=expected), self.assertRaises(HTTPException) as failure:
                    self.compress(boundary, expected)
                self.assertEqual(failure.exception.status_code, 409)
            summary_model.bind.assert_not_called()

    def test_summary_budget_and_invalid_result_preserve_database(self):
        for count, content, finish, status in [
            (56321, "answer", "stop", 400),
            (100, "partial", "length", 502),
            (100, "  ", "stop", 502),
            (100, [], "stop", 502),
        ]:
            with self.subTest(count=count, content=content, finish=finish):
                summary_model = model(content, finish)
                with patch.object(context, "llm", summary_model), patch.object(context, "count_message_tokens", return_value=count):
                    with self.assertRaises(HTTPException) as failure:
                        self.compress(4)
                self.assertEqual(failure.exception.status_code, status)
                self.db.expire_all()
                conversation = self.db.get(Conversation, 1)
                self.assertIsNone(conversation.summary)
                self.assertIsNone(conversation.summarized_until_id)
                if status == 400:
                    summary_model.bind.assert_not_called()
                else:
                    summary_model.bind.assert_called_once_with(max_tokens=8192)

    def test_summary_at_exact_budget_and_counted_content_matches(self):
        summary_model = model("summary")
        with patch.object(context, "llm", summary_model), patch.object(context, "count_message_tokens", return_value=56320) as counter:
            self.assertEqual(self.compress(4), "summary")
        self.assertEqual([m["content"] for m in counter.call_args.args[0]], [m.content for m in summary_model.invoke.call_args.args[0]])
        self.assertEqual(self.db.get(Conversation, 1).summarized_until_id, 4)

    def test_rewrite_summary_only_budget_and_truncation(self):
        for count, finish, status in [(56320, "stop", None), (56321, "stop", 400), (100, "length", 502)]:
            with self.subTest(count=count, finish=finish):
                rewrite_model = model("rewritten", finish)
                with patch.object(search, "llm", rewrite_model), patch.object(search, "count_message_tokens", return_value=count):
                    if status:
                        with self.assertRaises(HTTPException) as failure:
                            search.rewrite_query("question", [], summary="summary")
                        self.assertEqual(failure.exception.status_code, status)
                    else:
                        self.assertEqual(search.rewrite_query("question", [], summary="summary"), "rewritten")
                        self.assertIn("summary", rewrite_model.invoke.call_args.args[0][1].content)
                if status == 400:
                    rewrite_model.bind.assert_not_called()

    def test_answer_budget_after_compression(self):
        self.add_turns(1, 6)
        for count, status in [(23552, None), (23553, 400)]:
            with self.subTest(count=count), patch.object(answers, "context_compress", return_value="summary"):
                answer_model = model()
                if status:
                    with self.assertRaises(HTTPException) as failure:
                        self.ask([24000, count], answer_model)
                    self.assertEqual(failure.exception.status_code, status)
                    answer_model.bind.assert_not_called()
                else:
                    self.assertEqual(self.ask([24000, count], answer_model)["answer"], "answer")

    def test_failed_answer_never_saves_turn(self):
        for content, finish in [("partial", "length"), ("", "stop"), ([], "stop")]:
            with self.subTest(content=content, finish=finish):
                request = SearchRequest(query="question", conversation_id=1, candidate_k_percentage=0.4)
                with patch.object(answers, "llm", model(content, finish)), \
                     patch.object(answers, "count_message_tokens", return_value=100), \
                     patch.object(school_qa, "plan_assignment_query", return_value=None), \
                     patch.object(rag_flow, "_scope_has_materials", return_value=True), \
                     patch.object(rag_flow, "needs_materials", return_value=True), \
                     patch.object(rag_flow, "search_vector", return_value=[]), \
                     patch.object(rag_flow, "search_bm25", return_value=[]), \
                     patch.object(rag_flow, "rerank_results", return_value=self.chunk):
                    with self.assertRaises(HTTPException) as failure:
                        with patch.object(rag_flow, "translate_query", return_value=None):
                            rag_flow.execute_qa(1, request, self.db, self.db.get(User, 1))
                self.assertEqual(failure.exception.status_code, 502)
                self.assertEqual(list(self.db.scalars(select(Message))), [])

    def test_project_ownership(self):
        with self.assertRaises(HTTPException) as failure:
            answers.get_history_messages(1, self.db, 999)
        self.assertEqual(failure.exception.status_code, 404)

    def test_tokenizer_includes_generation_prompt(self):
        tokenizer = Mock()
        tokenizer.apply_chat_template.return_value = [1, 2, 3]
        messages = [{"role": "user", "content": "text"}]
        with patch.object(context, "get_tokenizer", return_value=tokenizer):
            self.assertEqual(context.count_message_tokens(messages), 3)
        tokenizer.apply_chat_template.assert_called_once_with(messages, tokenize=True, add_generation_prompt=True)

    def test_overview_prompt_and_sources_include_body_after_cover(self):
        answer_model = model("overview [2]")
        chunks = [dict(self.chunk[0], parent_content="# Cover"),
                  dict(self.chunk[0], parent_id=999, parent_content="# Exercises\nFinal exercise")]
        with patch.object(answers, "llm", answer_model), \
             patch.object(answers, "count_message_tokens", return_value=100), \
             patch.object(context, "count_message_tokens", return_value=100):
            result = answers.generate_answer("总结这份 PPT", chunks, [], None, self.db, 1, overview=True)
        messages = answer_model.invoke.call_args.args[0]
        self.assertIn("整份文档概述", messages[0].content)
        self.assertIn("Final exercise", messages[1].content)
        self.assertEqual(result["sources"][1]["parent_id"], 999)
        self.assertEqual(result["retrieved_contexts"], [c["parent_content"] for c in chunks])


if __name__ == "__main__":
    unittest.main(verbosity=2)
