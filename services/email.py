"""SMTP 이메일 발송"""
import smtplib
import logging
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from services.config_service import get_config

logger = logging.getLogger(__name__)


def _get_smtp_config() -> dict:
    return {
        'host': get_config('smtp_host'),
        'port': int(get_config('smtp_port') or 587),
        'user': get_config('smtp_user'),
        'password': get_config('smtp_password'),
        'from': get_config('smtp_from'),
    }


def send_email(to: str, subject: str, html_body: str) -> bool:
    cfg = _get_smtp_config()
    if not cfg['host'] or not cfg['user']:
        logger.warning('[EMAIL] SMTP 설정 없음 — 발송 건너뜀')
        return False

    msg = MIMEMultipart('alternative')
    msg['Subject'] = subject
    msg['From'] = cfg['from'] or cfg['user']
    msg['To'] = to
    msg.attach(MIMEText(html_body, 'html', 'utf-8'))

    try:
        with smtplib.SMTP(cfg['host'], cfg['port'], timeout=10) as smtp:
            smtp.ehlo()
            smtp.starttls()
            smtp.login(cfg['user'], cfg['password'])
            smtp.sendmail(msg['From'], [to], msg.as_string())
        logger.info(f'[EMAIL] 발송 완료 → {to}')
        return True
    except Exception as e:
        logger.error(f'[EMAIL] 발송 실패: {e}')
        return False


def send_password_reset_email(to: str, reset_url: str) -> bool:
    subject = '[매실 스튜디오] 비밀번호 재설정 링크'
    html = f"""
    <div style="font-family:sans-serif;max-width:480px;margin:0 auto">
      <h2 style="color:#e8355a">비밀번호 재설정</h2>
      <p>아래 버튼을 클릭해 새 비밀번호를 설정하세요. 링크는 1시간 후 만료됩니다.</p>
      <a href="{reset_url}"
         style="display:inline-block;background:#e8355a;color:#fff;padding:12px 28px;
                border-radius:8px;text-decoration:none;font-weight:600;margin:16px 0">
        비밀번호 재설정
      </a>
      <p style="color:#888;font-size:12px">본인이 요청하지 않은 경우 이 메일을 무시하세요.</p>
    </div>
    """
    return send_email(to, subject, html)
