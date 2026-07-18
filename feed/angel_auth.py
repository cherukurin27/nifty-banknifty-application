"""
feed/angel_auth.py — Angel One SmartAPI authentication with TOTP.
Returns a logged-in SmartConnect session object.
"""

import pyotp
import logzero
from logzero import logger
from SmartApi import SmartConnect

import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
import config

logzero.logfile(os.path.join(config.LOG_DIR, "auth.log"), maxBytes=1_000_000, backupCount=2)


def get_session() -> tuple[SmartConnect, str, str]:
    """
    Authenticate with Angel One SmartAPI using TOTP.

    Returns
    -------
    (SmartConnect, auth_token, feed_token)
    """
    totp_value = pyotp.TOTP(config.TOTP_SECRET).now()

    api = SmartConnect(api_key=config.API_KEY)
    data = api.generateSession(
        clientCode=config.CLIENT_ID,
        password=config.PASSWORD,
        totp=totp_value,
    )

    if data["status"] is False:
        raise RuntimeError(f"Angel One login failed: {data['message']}")

    auth_token = data["data"]["jwtToken"]
    feed_token = api.getfeedToken()

    logger.info("Angel One login successful — client: %s", config.CLIENT_ID)
    return api, auth_token, feed_token
