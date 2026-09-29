import httpx
import base64
import json
import logging
import os
from io import BytesIO

from langchain_openai import ChatOpenAI
from openai import APIError
from PIL import Image, UnidentifiedImageError

from sqlalchemy.orm import Session

from app.models.courses import Course
from app.models.course_timetable import CourseSchedule

from sqlalchemy import select
from langchain_core.messages import HumanMessage

logger = logging.getLogger(__name__)


class TimetableImageError(ValueError):
    """The upload is not a decodable PNG or JPEG image."""


class TimetableParseError(ValueError):
    """No usable timetable could be extracted from the image."""


class TimetableProviderError(RuntimeError):
    """The upstream vision service failed."""

LAYOUT_PARSING_URL = "https://open.bigmodel.cn/api/paas/v4/layout_parsing"

llm = ChatOpenAI(
    model="glm-4.5-flash",
    base_url="https://open.bigmodel.cn/api/paas/v4/",
    api_key=os.environ["ZHIPU_API_KEY"]
)

TEXT = """
你是一个课程表信息提取助手。下面会给你一段由 OCR 从课表图片转录的 Markdown 文本。
请只依据这段文本，提取其中出现的全部上课安排，并返回一个 JSON 数组。数组中的每个对象表示一次独立的上课安排。

每个对象必须且只能包含以下八个字段：

- course_name：课程名称，严格保留文本中的原始内容和大小写。

- course_code：课程编号；文本中没有则为 null。

- location：上课教室或地点。

- day_of_week：上课星期，只能是 "Mon"、"Tue"、"Wed"、"Thu"、"Fri"、"Sat"、"Sun" 之一。

- start_time：开始时间，24 小时制 "HH:MM" 格式。

- end_time：结束时间，24 小时制 "HH:MM" 格式。

- teachers：任课教师；文本中没有则为 null。

- units：学分，必须是数字；无法确认时为 null，不得返回数字字符串。

提取规则：

1. 只返回 JSON 数组本身，不要任何解释、前后缀文字或代码块（```）。

2. 文本可能是两种形式之一，或两者同时出现：
   - 网格时间表：按星期分列、按时间段分行，课程单元格里写有课程名和教室。
   - Sections 表：包含 Course Code、Name、Teachers、Time、Room、Units 等列。
   两种形式同时出现时，以网格时间表确定星期与起止时间，以 Sections 表补齐 course_code、teachers、units。

3. 同一门课在不同天上课，每一天拆成一条独立记录。

4. 同一课程在同一天、同一地点且时间段连续时，合并为一条，start_time 取最早、end_time 取最晚；时间不连续或地点不同时分别成条。

5. 逐条扫描 Mon 到 Sun，以及 Sections 表中的每一行，不得遗漏。

6. 【最重要】严禁使用文本以外的任何信息，严禁推测、补全或凭常识编造。
   字段在文本中没有明确出现时，一律设为 null。例如时间表网格中没有教师姓名，则 teachers 必须为 null。

7. 含义相同但写法不同的字段名统一为标准字段：teacher/instructor → teachers；courseName/course_title → course_name；courseCode/code → course_code；room/classroom → location。

8. 输出前逐个核对：字段名与上述八个完全一致，无缺漏、无多余字段；day_of_week 合法；时间为 "HH:MM"；units 为数字或 null。

示例输出：
[
  {
    "course_name": "Machine Learning",
    "course_code": "COMP2073",
    "location": "T8-209",
    "day_of_week": "Mon",
    "start_time": "19:00",
    "end_time": "21:00",
    "teachers": "Ms. Angela",
    "units": 3.0
  }
]
"""

EXPECTED_ATTRIBUTES = {
    "course_name",
    "course_code",
    "location",
    "day_of_week",
    "start_time",
    "end_time",
    "teachers",
    "units",
}
NECESSARY_ATTRIBUTES = [
    "course_name",
    "location",
    "day_of_week",
    "start_time",
    "end_time",
]

async def ocr_image(content: bytes, content_type: str) -> str:
    """调用 GLM-OCR，把图片转录为 Markdown 文本。"""
    validate_image(content, content_type)
    processed_content = base64.b64encode(content).decode("utf-8")
    try:
        async with httpx.AsyncClient(timeout=120) as client:
            response = await client.post(
                LAYOUT_PARSING_URL,
                headers={
                    "Authorization": f"Bearer {os.environ['ZHIPU_API_KEY']}"
                },
                json={
                    "model": "glm-ocr",
                    "file": f"data:{content_type};base64,{processed_content}",
                },
            )
            response.raise_for_status()
    except httpx.HTTPError as exc:
        logger.exception("GLM-OCR request failed")
        raise TimetableProviderError(
            "课表识别服务暂时不可用，请稍后重试"
        ) from exc
    #返回一个md 传给llm解析出json
    return response.json()["md_results"]

async def timetable_service(content: bytes, db: Session, content_type: str, user_id: int):
    complete, timetables = await content_parse(content, content_type)
    await timetable_database(timetables, db, user_id)
    if complete:
        return{
            "message": "课表解析成功",
            "timetables": timetables
        }
    else:
        return("课表渲染失败，再上传一次图片")
    
def add_schedule(course, timetable, db):
    """把数据加到schedule表"""
    schedule = CourseSchedule()
    schedule.course_id = course.id 
    schedule.day_of_week = timetable.get("day_of_week")              
    schedule.start_time = timetable.get("start_time")
    schedule.end_time = timetable.get("end_time")
    schedule.location = timetable.get("location")
    db.add(schedule)

def add_course(db, timetable, course_name, course_code, user_id: int):
    """把数据加到course表"""
    course = Course()
    course.user_id = user_id
    course.course_name = course_name
    course.course_code = course_code
    course.teachers = timetable.get("teachers")
    course.units = timetable.get("units")
    db.add(course)
    db.flush()
    return course

def check_duplicate(db, timetable, course):
    """检查是否schedule表中是不是已经加入了重复信息"""
    if db.scalar(select(CourseSchedule).where(        
        CourseSchedule.course_id == course.id,
        CourseSchedule.day_of_week == timetable.get("day_of_week"),
        CourseSchedule.start_time == timetable.get("start_time"),
        CourseSchedule.end_time == timetable.get("end_time"),
        CourseSchedule.location == timetable.get("location"),)):
        return True
    return False

def data_update(course, timetable):
    """二次上传补齐不必要字段"""
    if course.course_code is None and timetable.get("course_code"):
        course.course_code = timetable.get("course_code")

    if course.teachers is None and timetable.get("teachers"):
        course.teachers = timetable.get("teachers")

    if course.units is None and timetable.get("units") is not None:
        course.units = timetable.get("units")

async def timetable_database(timetables:list, db:Session, user_id: int):
        """数据入库"""
        try:
            for timetable in timetables:
                course_code = timetable.get("course_code")
                if course_code:
                    course = db.scalar(select(Course).where(
                        Course.user_id == user_id,
                        Course.course_code == course_code,
                    ))
                    if course:
                        data_update(course, timetable)
                        if check_duplicate(db, timetable, course):
                            continue
                        
                        add_schedule(course, timetable, db)
                    else:
                        course_name = timetable.get("course_name")
                        course = db.scalar(select(Course).where(
                            Course.user_id == user_id,
                            Course.course_name == course_name,
                        ))
                        if not course:
                            course = add_course(db, timetable, course_name, course_code, user_id)
                        
                        data_update(course, timetable)

                        if check_duplicate(db, timetable, course):
                            continue                     
                        add_schedule(course, timetable, db)
                else:
                        course_name = timetable.get("course_name")
                        course = db.scalar(select(Course).where(
                            Course.user_id == user_id,
                            Course.course_name == course_name,
                        ))
                        if not course:
                            course = add_course(db, timetable, course_name, course_code, user_id)
                        
                        data_update(course, timetable)

                        if check_duplicate(db, timetable, course):
                                continue
                        add_schedule(course, timetable, db)         
            db.commit()
        except Exception:
            db.rollback()
            raise
    
       
       
   
    
def validate_image(content: bytes, content_type: str):
    """Validate the actual image before sending it to the vision service."""
    expected_format = {"image/png": "PNG", "image/jpeg": "JPEG"}.get(content_type)
    if expected_format is None:
        raise TimetableImageError("图片类型错误，请上传 PNG 或 JPEG 图片")
    try:
        with Image.open(BytesIO(content)) as image:
            if image.format != expected_format:
                raise TimetableImageError("图片内容与文件类型不符，请重新上传 PNG 或 JPEG 图片")
            image.verify()
        # verify() checks the file structure; load() also detects truncated pixel data.
        with Image.open(BytesIO(content)) as image:
            image.load()
    except (UnidentifiedImageError, OSError, SyntaxError, ValueError,
            Image.DecompressionBombError) as exc:
        if isinstance(exc, TimetableImageError):
            raise
        raise TimetableImageError("图片已损坏或无法读取，请重新上传完整的 PNG 或 JPEG 图片") from exc


async def content_parse(content: bytes, content_type: str):
    """图片解析；输入、识别和上游调用错误分别处理。"""
    ocr_markdown = await ocr_image(content, content_type)
    message = HumanMessage(
        content=(
            {"type": "text", "text": TEXT},
            {
             "type" : "text" ,
             "text" : ( "以下是课表图片的OCR转录结果，" f"请严格依据其中的内容提取：\n {ocr_markdown} " ),
            },
    )
    )
    try:
        response = await llm.ainvoke([message])
    except APIError as exc:
        logger.exception("Timetable vision service request failed")
        raise TimetableProviderError("课表识别服务暂时不可用，请稍后重试") from exc

    retry_message = "未能从md文本中识别出药效信息，请上传包含课程名称、地点和上课时间的清晰图片"
    response_content = response.content
    if not isinstance(response_content, str) or not response_content.strip():
        raise TimetableParseError(retry_message)

    # 根据 [] 分割 JSON，兼容模型返回的代码块和说明文字。
    start = response_content.find("[")
    end = response_content.rfind("]")
    if start == -1 or end == -1:
        raise TimetableParseError(retry_message)
    try:
        timetables = json.loads(response_content[start:end + 1])
    except json.JSONDecodeError as exc:
        raise TimetableParseError(retry_message) from exc
    if not isinstance(timetables, list) or not timetables:
        raise TimetableParseError(retry_message)

    valid_timetables = []
    for timetable in timetables:
        if not isinstance(timetable, dict):
            continue
        if set(timetable.keys()) != EXPECTED_ATTRIBUTES:
            continue
        if all(timetable.get(key) for key in NECESSARY_ATTRIBUTES):
            valid_timetables.append(timetable)

    if not valid_timetables:
        raise TimetableParseError(retry_message)
    return len(valid_timetables) == len(timetables), valid_timetables
