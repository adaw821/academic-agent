import base64
import json

from langchain_openai import ChatOpenAI

llm = ChatOpenAI(
    model=,
    base_url=,
    api_key=
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

async def timetable_service(content: bytes):
    await content_parse(content)
    
async def content_parse(content: bytes):
    try:
        if not content:
            raise ValueError("图片不能为空")
        
        img_base64 = base64.b64encode(content)
        processed_content = img_base64.decode("utf-8")
        
        response = await llm.ainvoke(processed_content, TEXT)
        
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
        
        complete = False
        if len(valid_timetables) == len(timetable):
            complete=True
        return complete, valid_timetables