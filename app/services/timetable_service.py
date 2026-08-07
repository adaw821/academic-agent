import base64
import json
import os

from langchain_openai import ChatOpenAI

from sqlalchemy.orm import Session

from app.models.courses import Course
from app.models.course_timetable import CourseSchedule

from sqlalchemy import select
from langchain_core.messages import HumanMessage

llm = ChatOpenAI(
    model="glm-4.6v",
    base_url="https://open.bigmodel.cn/api/paas/v4/",
    api_key=os.environ["ZHIPU_API_KEY"]
)

TEXT="""

你是一个课程表图片解析助手。你需要从收到的课程表图片中提取全部课程信息。

必须返回一个 JSON 数组，数组中的每个对象表示一次独立的上课安排，并且必须保留以下全部字段：

- course_name：课程名称，严格保留图片中的原始内容，尤其是字母大小写。
- course_code：课程编号。
- location：上课教室或地点。
- day_of_week：上课星期，只能是 "Mon"、"Tue"、"Wed"、"Thu"、"Fri"、"Sat"、"Sun" 之一。
- start_time：开始时间，必须使用 24 小时制的 "HH:MM" 格式。
- end_time：结束时间，必须使用 24 小时制的 "HH:MM" 格式。
- teachers：任课教师。
- units：课程学分，必须是数字。

1. 只返回 JSON 数组本身,不要任何多余文字,不要解释,不要用代码块(```)包裹。
2. day_of_week 只能返回 "Mon"、"Tue"、"Wed"、"Thu"、"Fri"、"Sat"、"Sun" 之一，不得返回中文或数字。
3. 同一门课如果在不同的天上课,每一天单独拆成一条记录。
4. 同一课程在同一天、同一地点并且时间连续时，合并为一条记录，start_time 取最早时间，end_time 取最晚时间。时间不连续或上课地点不同时，必须分别返回独立对象。
5. 禁止猜测图片中不存在或无法确认的信息。任何无法识别的字段都必须保留，并将值设为 null。units 必须是数字；无法识别时为 null，不得返回数字字符串。
6. 先从 Mon 到 Sun 逐列扫描上方课表中的所有非空单元格，不得遗漏任何课程。
7. 再读取图片下方的 Sections 表，获得每门课程的 course_code、teachers 和 units，并与上方课表逐门核对。确保 Sections 表中出现的每门课程都至少有一条上课安排。
8. 输出 JSON 前再次核对每个星期和每门课程；如果发现遗漏，按照以上规则补充后再输出。

9.字段名校验规则：

1. 每个对象必须且只能包含以下八个字段：
   "course_name"、"course_code"、"location"、"day_of_week"、
   "start_time"、"end_time"、"teachers"、"units"。

2. 输出前必须逐个检查字段名，并把含义相同但拼写不同的字段统一为上述标准字段。例如：
   - teacher、teacher_name、instructor → teachers
   - courseName、course_title → course_name
   - courseCode、code → course_code
   - room、classroom、locate → location

3. 不允许在最终结果中保留标准字段以外的任何字段。

4. 如果无法判断某个非标准字段对应哪个标准字段，不要猜测或保留该字段；使用正确的标准字段名，并将值设为 null。

5. 输出最终 JSON 前，再次确认每个对象的字段名与上述八个字段完全一致，不得缺少、增加或修改字段名

示例输出：
[
  {
    "course_name": "Machine Learning",
    "course_code": "COMP2073",
    "location": "t8-209",
    "day_of_week": "Mon",
    "start_time": "19:00",
    "end_time": "21:00",
    "teachers": "Ms.Angela",
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

async def timetable_service(content: bytes, db: Session, content_type: str):
    complete, timetables = await content_parse(content, content_type)
    await timetable_database(timetables, db)
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

def add_course(db, timetable, course_name, course_code):
    """把数据加到course表"""
    course = Course()
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

async def timetable_database(timetables:list, db:Session):
        """数据入库"""
        try:
            for timetable in timetables:
                course_code = timetable.get("course_code")
                if course_code:
                    course = db.scalar(select(Course).where(Course.course_code == course_code))
                    if course:
                        data_update(course, timetable)
                        if check_duplicate(db, timetable, course):
                            continue
                        
                        add_schedule(course, timetable, db)
                    else:
                        course_name = timetable.get("course_name")
                        course = db.scalar(select(Course).where(Course.course_name==course_name))
                        if not course:
                            course = add_course(db, timetable, course_name, course_code)
                        
                        data_update(course, timetable)

                        if check_duplicate(db, timetable, course):
                            continue                     
                        add_schedule(course, timetable, db)
                else:
                        course_name = timetable.get("course_name")
                        course = db.scalar(select(Course).where(Course.course_name==course_name))
                        if not course:
                            course = add_course(db, timetable, course_name, course_code)
                        
                        data_update(course, timetable)

                        if check_duplicate(db, timetable, course):
                                continue
                        add_schedule(course, timetable, db)         
            db.commit()
        except Exception:
            db.rollback()
            raise
    
       
       
   
    
async def content_parse(content: bytes, content_type: str):
    """图片解析"""
    try:
        if not content:
            raise ValueError("图片不能为空")
        
        img_base64 = base64.b64encode(content)
        processed_content = img_base64.decode("utf-8")
        
        message = HumanMessage(
            content=[
                {
                    "type": "text",
                    "text": TEXT
                },
                
                {
                    "type": "image_url",
                    "image_url": {
                        "url": (
                            f"data:{content_type};"
                            f"base64,{processed_content}"
                        )
                    },
                }
            ]
        )
        response = await llm.ainvoke([message])
        
        response_content = response.content
        if not response_content:
            raise ValueError("没有解析出课表内容")
        
        #根据[]分割json，防止返回出现别的
        start = response_content.find("[")
        end = response_content.rfind("]")
        if start==-1 or end==-1:
            raise ValueError("json解析有问题")
        json_text =response_content[start:end+1]
        timetables = json.loads(json_text)
        if not isinstance(timetables, list) or not timetables:
            raise ValueError("课程表为空")
        
        valid_timetables = []

        for timetable in timetables:
            if not isinstance(timetable, dict):
                continue
            
            empty = False

            if set(timetable.keys()) != EXPECTED_ATTRIBUTES:
                continue

            for key in NECESSARY_ATTRIBUTES:
                value = timetable.get(key)

                if not value:
                    empty = True
                    break

            if not empty:
                valid_timetables.append(timetable)
            
        if not valid_timetables:
            raise ValueError("所有课程都缺少必要字段")
        
        #返回解析是否完全， 和有效字段课程表
        return len(valid_timetables) == len(timetables), valid_timetables
    except Exception:
        raise ValueError("解析图片失败")
