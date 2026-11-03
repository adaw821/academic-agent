"""Safe public image diagnostics and document-level tolerance policy."""
import os


class TooManyImageFailures(ValueError):
    def __init__(self, summary):
        self.summary = summary
        super().__init__('图片识别失败过多')


def failure_reason(exc):
    # Only fixed labels leave the worker; exception strings may contain credentials.
    text = str(exc)
    if '429' in text:
        return '识别服务限流'
    if '内容过滤' in text:
        return '识别服务过滤了图片内容'
    if 'ZHIPU_API_KEY' in text:
        return '图片识别服务未配置'
    if '像素' in text:
        return '图片像素不可用'
    if '为空' in text:
        return '识别结果为空'
    if '长度' in text:
        return '识别结果被截断'
    if '网络' in text or 'Timeout' in type(exc).__name__:
        return '识别服务连接失败'
    return '图片识别失败'


def summarize_images(records):
    attempted = [r for r in records if r.get('router') != 'skip']
    failures = [dict(picture_id=r['id'], pages=r.get('pages', []), reason=r['recognition_error'])
                for r in attempted if r.get('recognition_error')]
    return {'total': len(attempted), 'failed': len(failures), 'failures': failures}


def check_image_failures(summary):
    minimum = int(os.environ.get('IMAGE_FAILURE_MIN_COUNT', '3'))
    maximum = int(os.environ.get('IMAGE_FAILURE_MAX_COUNT', '10'))
    ratio = float(os.environ.get('IMAGE_FAILURE_RATIO', '0.5'))
    if minimum < 1 or maximum < minimum or not 0 <= ratio <= 1:
        raise ValueError('图片失败阈值配置无效')
    failed, total = summary['failed'], summary['total']
    if failed >= maximum or (failed >= minimum and total and failed / total > ratio):
        raise TooManyImageFailures(summary)
