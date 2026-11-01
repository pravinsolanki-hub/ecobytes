"""
EcoBytes Cellular SMS Dispatcher Module
Provides carrier-grade SMS dispatch to Indian cellular networks via Fast2SMS and global networks via Twilio.
Uses pure Python 3.14 standard library (urllib.request, json) with zero external pip dependencies.
"""

import urllib.request
import urllib.parse
import json
import re

def clean_phone_10_digits(phone_raw):
    """Extract exactly 10 digits from any raw phone format."""
    digits = re.sub(r"\D", "", str(phone_raw or ""))
    if len(digits) == 12 and digits.startswith("91"):
        digits = digits[2:]
    elif len(digits) == 11 and digits.startswith("0"):
        digits = digits[1:]
    return digits if len(digits) == 10 else ""

def send_fast2sms(api_key, phone_10_digit, message_text):
    """
    Sends real SMS via Fast2SMS Quick (q) route to Indian cellular numbers.
    Returns: (success: bool, info: dict)
    """
    clean_phone = clean_phone_10_digits(phone_10_digit)
    if not clean_phone:
        return False, {"error": f"Invalid Indian 10-digit mobile number: {phone_10_digit}"}

    if not api_key or not api_key.strip():
        return False, {"error": "Fast2SMS API Key is missing. Please add your API key in Gateway Settings."}

    # Fast2SMS Quick Route API endpoint
    url = "https://www.fast2sms.com/dev/bulkV2"
    payload = {
        "route": "q",
        "message": message_text,
        "language": "english",
        "flash": 0,
        "numbers": clean_phone
    }
    
    data_bytes = json.dumps(payload).encode("utf-8")
    headers = {
        "authorization": api_key.strip(),
        "Content-Type": "application/json",
        "User-Agent": "EcoBytes-SIH-AQI-AlertEngine/3.0"
    }

    req = urllib.request.Request(url, data=data_bytes, headers=headers, method="POST")

    try:
        with urllib.request.urlopen(req, timeout=12) as response:
            res_body = response.read().decode("utf-8")
            data = json.loads(res_body)

            if data.get("return") is True:
                req_id = data.get("request_id") or "req_live"
                msg_info = data.get("message", ["SMS sent successfully"])[0]
                return True, {
                    "provider": "Fast2SMS",
                    "status": "DELIVERED (CARRIER)",
                    "request_id": req_id,
                    "recipient": f"+91-{clean_phone}",
                    "message": msg_info
                }
            else:
                error_msg = data.get("message", "Fast2SMS dispatch rejected")
                if isinstance(error_msg, list):
                    error_msg = ", ".join(error_msg)
                return False, {
                    "provider": "Fast2SMS",
                    "status": "FAILED",
                    "error": str(error_msg),
                    "recipient": f"+91-{clean_phone}"
                }
    except urllib.error.HTTPError as e:
        err_body = e.read().decode("utf-8") if e.fp else str(e)
        try:
            err_json = json.loads(err_body)
            err_detail = err_json.get("message", err_body)
            if isinstance(err_detail, list):
                err_detail = ", ".join(err_detail)
        except Exception:
            err_detail = err_body
        return False, {
            "provider": "Fast2SMS",
            "status": "HTTP_ERROR",
            "code": e.code,
            "error": f"Fast2SMS API Error ({e.code}): {err_detail}",
            "recipient": f"+91-{clean_phone}"
        }
    except Exception as e:
        return False, {
            "provider": "Fast2SMS",
            "status": "NETWORK_ERROR",
            "error": f"Connection error reaching Fast2SMS gateway: {str(e)}",
            "recipient": f"+91-{clean_phone}"
        }

def send_twilio(account_sid, auth_token, from_number, to_phone, message_text):
    """
    Sends real SMS via Twilio REST API.
    Returns: (success: bool, info: dict)
    """
    import base64
    clean_phone = clean_phone_10_digits(to_phone)
    if not clean_phone:
        return False, {"error": "Invalid recipient phone number"}

    to_e164 = f"+91{clean_phone}"
    url = f"https://api.twilio.com/2010-04-01/Accounts/{account_sid}/Messages.json"
    
    post_data = urllib.parse.urlencode({
        "To": to_e164,
        "From": from_number,
        "Body": message_text
    }).encode("utf-8")

    creds = f"{account_sid}:{auth_token}"
    auth_b64 = base64.b64encode(creds.encode("utf-8")).decode("utf-8")
    
    headers = {
        "Authorization": f"Basic {auth_b64}",
        "Content-Type": "application/x-www-form-urlencoded"
    }

    req = urllib.request.Request(url, data=post_data, headers=headers, method="POST")

    try:
        with urllib.request.urlopen(req, timeout=12) as response:
            res_body = response.read().decode("utf-8")
            data = json.loads(res_body)
            sid = data.get("sid", "unknown_sid")
            return True, {
                "provider": "Twilio",
                "status": "DELIVERED (CARRIER)",
                "request_id": sid,
                "recipient": to_e164
            }
    except Exception as e:
        return False, {
            "provider": "Twilio",
            "status": "ERROR",
            "error": str(e),
            "recipient": to_e164
        }

def dispatch_carrier_sms(phone, message_text, gateway_config):
    """
    Master dispatch router.
    Checks gateway_config; if live mode is enabled, routes to Fast2SMS or Twilio.
    Returns dict with dispatch results.
    """
    if not gateway_config or not gateway_config.get("is_live"):
        return {
            "success": True,
            "mode": "SIMULATED",
            "provider": "Sandbox Simulation",
            "recipient": f"+91-{clean_phone_10_digits(phone)}",
            "message": "Simulated carrier dispatch (Gateway sandbox mode active)"
        }

    provider = gateway_config.get("provider", "fast2sms").lower()

    if provider == "fast2sms":
        api_key = gateway_config.get("api_key", "")
        success, info = send_fast2sms(api_key, phone, message_text)
        info["mode"] = "LIVE_CARRIER" if success else "FAILED"
        info["success"] = success
        return info

    elif provider == "twilio":
        sid = gateway_config.get("twilio_sid", "")
        token = gateway_config.get("twilio_token", "")
        from_num = gateway_config.get("twilio_from", "")
        success, info = send_twilio(sid, token, from_num, phone, message_text)
        info["mode"] = "LIVE_CARRIER" if success else "FAILED"
        info["success"] = success
        return info

    return {
        "success": False,
        "mode": "FAILED",
        "error": f"Unknown gateway provider: {provider}"
    }
