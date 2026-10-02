from markupsafe import soft_unicode
from jinja2 import Markup, Environment
import urllib3
from urllib3.util.retry import Retry

retry = Retry(total=3, method_whitelist=["GET"])
pool = urllib3.PoolManager(retries=urllib3.Retry(3, method_whitelist=["GET"]))
env = Environment()
s = soft_unicode("x")
m = Markup("<b>hi</b>")
