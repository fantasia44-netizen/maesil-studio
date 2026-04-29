"""입력값 검증"""
import re


def validate_email(email: str) -> str:
    if not email:
        return ''
    email = email.strip().lower()
    if re.match(r'^[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}$', email):
        return email
    return ''


def validate_password(password: str) -> str:
    """에러 메시지 반환, 없으면 빈 문자열"""
    if len(password) < 8:
        return '비밀번호는 8자 이상이어야 합니다.'
    if not re.search(r'[A-Za-z]', password):
        return '비밀번호에 영문자를 포함해야 합니다.'
    if not re.search(r'[0-9!@#$%^&*]', password):
        return '비밀번호에 숫자 또는 특수문자를 포함해야 합니다.'
    return ''
