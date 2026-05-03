"""
Workday condition evaluator.

Queries the Timor.tech holiday API to determine whether a given date
is a workday, a weekend day, a holiday, or a make-up workday.
"""
import datetime
import logging
from functools import cache

from tenacity import retry, stop_after_attempt, wait_exponential

import requests
from fake_useragent import UserAgent

from .base_evaluator import BaseEvaluator


logger = logging.getLogger(__name__)


user_agent = UserAgent().random


@cache
@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=1, max=4))
def is_workday(date: str | datetime.date) -> bool | None:
    """
    check whether given date is workday
    """
    date_str = date if isinstance(date, str) else date.strftime("%Y-%m-%d")
    url = f"http://timor.tech/api/holiday/info/{date_str}"
    try:
        response = requests.get(url, headers = {'User-Agent': user_agent}, timeout=5)
        data = response.json()

        if data.get("code") == 0:
            # type: 0=workday, 1=weekend, 2=holiday, 3=make-up workday
            day_type = data["type"]["type"]

            # Workdays (type=0) and make-up workdays (type=3) are both days to work
            return day_type == 0 or day_type == 3
        else:
            logger.error(f"workday api respond error: {data}")
            return None

    except Exception as e:
        logger.error(f"workday api request failed: {e}")
        logger.exception(e)
        return None
    

class WorkdayEvaluator(BaseEvaluator):

    def __call__(self, param: bool) -> bool:
        "Check if today is workday. Return False if error encountered."
        if not isinstance(param, bool):
            raise ValueError(f"invalid {self.__class__.__name__} param")
        should_today_be_workday: bool = param
        res = is_workday(datetime.datetime.now().date()) 
        if res is None:
            return False
        return res ==  should_today_be_workday
    