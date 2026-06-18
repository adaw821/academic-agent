"""Test script for parse_sections()"""
import sys
sys.path.insert(0, '.')
from ocr_server import parse_sections

# Sample Baidu OCR output (merged format - all fields in one line)
sample_ocr_merged = """
Sections - Timetable for 2025-2026 Semester 1
Course Category   Course Code   Course Name   Teacher   Time   Room   Units
MR   CMP2073   Computer Graphics   Dr.Weipeng   Tue14:00-15:40   T7-302   3.0
MR   CMP2073   Computer Graphics   Dr.Weipeng   Mon15:00-16:40   T3-405   3.0
CORE   GCAP2013   Academic Writing   Prof.LIN   Wed18:00-20:50   T1-201   3.0
"""

# Sample Baidu OCR output (table format - each cell is a line)
sample_ocr_table = """
Sections - Timetable for 2025-2026 Semester 1
Course Category
Course Code
Course Name
Teacher
Time
Room
Units
MR
CMP2073
Computer Graphics
Dr.Weipeng
Tue 14:00-15:40
T7-302
3.0
CORE
GCAP2013
Academic Writing
Prof.LIN
Wed 18:00-20:50
T1-201
3.0
"""

print("="*60)
print("Test 1: Merged format (all fields in one line)")
print("="*60)
results1 = parse_sections(sample_ocr_merged)
print(f"\nFound {len(results1)} courses:")
for r in results1:
    print(f"  - {r['name']} ({r['code']}) @ {r['day']} {r['start_time']}-{r['end_time']} in {r['location']}")
    if 'all_times' in r:
        print(f"    All time slots: {len(r['all_times'])}")

print("\n" + "="*60)
print("Test 2: Table format (each cell is a line)")
print("="*60)
results2 = parse_sections(sample_ocr_table)
print(f"\nFound {len(results2)} courses:")
for r in results2:
    print(f"  - {r['name']} ({r['code']}) @ {r['day']} {r['start_time']}-{r['end_time']} in {r['location']}")
    if 'all_times' in r:
        print(f"    All time slots: {len(r['all_times'])}")

print("\n" + "="*60)
print("Test 3: Realistic Baidu OCR output")
print("="*60)
# Simulate realistic Baidu OCR output (might have noise, formatting issues)
realistic_ocr = """
My Sections
2025-2026 Semester 1
1
MR
CMP2073
Computer Graphics
Dr.Weipeng
Tue 14:00 - 15:40
T7-302
3.0
2
CORE
GCAP2013
Academic Writing
Prof.LIN
Wed 18:00 - 20:50
T1-201
3.0
"""
results3 = parse_sections(realistic_ocr)
print(f"\nFound {len(results3)} courses:")
for r in results3:
    print(f"  - {r['name']} ({r['code']}) @ {r['day']} {r['start_time']}-{r['end_time']} in {r['location']}")

print("\nDone!")
