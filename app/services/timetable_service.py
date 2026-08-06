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

禁止猜测图片中不存在或无法确认的信息。任何无法识别的字段都必须保留，并将值设为 null。

同一门课程如果在不同星期或不同时间上课，每次上课安排必须分别返回一个独立的 JSON 对象。

只返回 JSON 数组本身，不要添加解释、前后缀文字或 Markdown 代码块。

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

UNNECCESSARY_ATTRIBUTE = ["teachers", "units", "course_code"]


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
    

async def timetable_database(timetables:list, db:Session):
        try:
            for timetable in timetables:
                course_code = timetable.get("course_code")
                if course_code:
                    course = db.scalar(select(Course).where(Course.course_code == course_code))
                    if course:
                        schedule = CourseSchedule()
                        schedule.course_id = course.id 
                        schedule.day_of_week = timetable.get("day_of_week")              
                        schedule.start_time = timetable.get("start_time")
                        schedule.end_time = timetable.get("end_time")
                        schedule.location = timetable.get("location")
                        db.add(schedule)
                    else:
                        course_name = timetable.get("course_name")
                        course = db.scalar(select(Course).where(Course.course_name==course_name))
                        if not course:
                            course = Course()
                            schedule = CourseSchedule()
                            course.course_name = course_name
                            course.course_code = course_code
                            course.teachers = timetable.get("teachers")
                            course.units = timetable.get("units")
                            db.add(course)
                            db.flush()
                            
                            schedule.course_id = course.id
                            schedule.day_of_week = timetable.get("day_of_week")              
                            schedule.start_time = timetable.get("start_time")
                            schedule.end_time = timetable.get("end_time")
                            schedule.location = timetable.get("location")
                            db.add(schedule)
                        else:
                            schedule = CourseSchedule()
                            schedule.course_id = course.id 
                            schedule.day_of_week = timetable.get("day_of_week")              
                            schedule.start_time = timetable.get("start_time")
                            schedule.end_time = timetable.get("end_time")
                            schedule.location = timetable.get("location")
                            db.add(schedule)
                else:
                        course_name = timetable.get("course_name")
                        course = db.scalar(select(Course).where(Course.course_name==course_name))
                        if not course:
                            course = Course()
                            schedule = CourseSchedule()
                            course.course_name = course_name
                            course.course_code = course_code
                            course.teachers = timetable.get("teachers")
                            course.units = timetable.get("units")
                            db.add(course)
                            db.flush()
                            
                            schedule.course_id = course.id
                            schedule.day_of_week = timetable.get("day_of_week")              
                            schedule.start_time = timetable.get("start_time")
                            schedule.end_time = timetable.get("end_time")
                            schedule.location = timetable.get("location")
                            db.add(schedule)
                        else:
                            schedule = CourseSchedule()
                            schedule.course_id = course.id 
                            schedule.day_of_week = timetable.get("day_of_week")              
                            schedule.start_time = timetable.get("start_time")
                            schedule.end_time = timetable.get("end_time")
                            schedule.location = timetable.get("location")
                            db.add(schedule)            
            db.commit()
        except Exception:
            db.rollback()
            raise
    
       
       
   
    
async def content_parse(content: bytes, content_type: str):
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
        
        valid_timetables=[]
        for timetable in timetables:
            empty = False
            for key, value in timetable.items():
                if value:
                    continue
                if key not in UNNECCESSARY_ATTRIBUTE:
                    empty = True
                    continue
            if not empty:
                valid_timetables.append(timetable)
        
        if not valid_timetables:
            raise ValueError("所有课程都缺少必要字段")
         
        return len(valid_timetables) == len(timetables), valid_timetables
    except Exception:
        raise ValueError("解析图片失败")
