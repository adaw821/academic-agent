"""
课程表 OCR 识别脚本 (Python 后端方案)
使用方法:
  1. 安装依赖: pip install pytesseract Pillow
  2. 安装 Tesseract OCR: https://github.com/UB-Mannheim/tesseract/releases
  3. 运行: python ocr_schedule.py <图片路径>
  4. 输出: JSON 格式的课程信息

用于 tesseract.js 无法工作时作为备选方案。
"""

import sys
import json
import re
from pathlib import Path

try:
    from PIL import Image
    import pytesseract
except ImportError:
    print(json.dumps({"error": "请先安装依赖: pip install pytesseract Pillow"}))
    sys.exit(1)

# 如果 Tesseract 不在 PATH 中，请取消注释并设置路径
# pytesseract.pytesseract.tesseract_cmd = r'C:\Program Files\Tesseract-OCR\tesseract.exe'


def ocr_image(image_path: str) -> str:
    """使用 Tesseract 识别图片中的文字"""
    img = Image.open(image_path)
    # 转换为灰度
    img = img.convert('L')
    # OCR with English
    text = pytesseract.image_to_string(img, lang='eng', config='--psm 6')
    return text


def parse_courses(ocr_text: str) -> list:
    """解析 OCR 文本为课程信息"""
    results = []
    lines = ocr_text.split('\n')

    period_times = {
        1: '08:00-09:40', 2: '10:00-11:40', 3: '12:00-13:30',
        4: '14:00-15:40', 5: '16:00-17:40'
    }

    current_day = None
    current_period = None

    for line in lines:
        line = line.strip()
        if not line or len(line) < 3:
            continue

        # Day detection
        day_map = {
            'mon': 'mon', 'monday': 'mon',
            'tue': 'tue', 'tuesday': 'tue', 'tues': 'tue',
            'wed': 'wed', 'wednesday': 'wed',
            'thu': 'thu', 'thursday': 'thu', 'thur': 'thu',
            'fri': 'fri', 'friday': 'fri',
        }
        line_lower = line.lower()
        for key, day in day_map.items():
            if line_lower.startswith(key):
                current_day = day
                break

        # Time detection
        time_match = re.match(r'(\d{1,2}):(\d{2})', line)
        if time_match:
            hour = int(time_match.group(1))
            if 8 <= hour < 10:
                current_period = 1
            elif 10 <= hour < 12:
                current_period = 2
            elif 12 <= hour < 14:
                current_period = 3
            elif 14 <= hour < 16:
                current_period = 4
            elif hour >= 16:
                current_period = 5

        # Room detection
        room_match = re.search(r'\b(T\d{1,2}-\d{3}|T\d{3}|C\d{3}|E\d{3})\b', line, re.IGNORECASE)
        if room_match and current_period:
            location = room_match.group(1).upper()
            # Extract course name
            name = re.sub(r'\b(T\d{1,2}-\d{3}|T\d{3}|C\d{3}|E\d{3})\b', '', line, flags=re.IGNORECASE)
            name = re.sub(r'\d{1,2}:\d{2}(-\d{1,2}:\d{2})?', '', name)
            name = re.sub(r'\b\d{4}\b', '', name)
            name = ' '.join(name.split()).strip()

            if name and len(name) > 2:
                results.append({
                    'name': name.strip('-–— '),
                    'day': current_day or 'mon',
                    'period': current_period,
                    'location': location
                })

    return results


def main():
    if len(sys.argv) < 2:
        # Try default test image
        test_path = Path(__file__).parent / 'test_schedule.png'
        if test_path.exists():
            image_path = str(test_path)
        else:
            print(json.dumps({
                "error": "用法: python ocr_schedule.py <图片路径>",
                "courses": [],
                "raw_text": ""
            }, ensure_ascii=False, indent=2))
            return
    else:
        image_path = sys.argv[1]

    if not Path(image_path).exists():
        print(json.dumps({"error": f"文件不存在: {image_path}"}, ensure_ascii=False))
        return

    try:
        raw_text = ocr_image(image_path)
        courses = parse_courses(raw_text)

        print(json.dumps({
            "raw_text": raw_text,
            "courses": courses,
            "count": len(courses)
        }, ensure_ascii=False, indent=2))
    except Exception as e:
        print(json.dumps({"error": str(e)}, ensure_ascii=False))


if __name__ == '__main__':
    main()
