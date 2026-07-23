import json


def validate_submission_photos(result, required_photos, photo_urls):
    try:
        photos = json.loads(photo_urls or '[]') if isinstance(photo_urls, str) else (photo_urls or [])
    except (TypeError, json.JSONDecodeError):
        photos = []
    photo_count = len([photo for photo in photos if photo])
    required_count = max(0, int(required_photos or 0))
    if result == 'normal' and photo_count < required_count:
        return f'现场照片不足：需拍 {required_count} 张，当前 {photo_count} 张'
    if result == 'abnormal' and photo_count < 1:
        return '异常项至少需要 1 张现场照片'
    return None
