import os
import sys
import json
import logging

from src.api import app


logging.basicConfig(**{
    'level': os.getenv('LOG_LEVEL', 'DEBUG'),
    'format': '{{"asctime": "{asctime}", "levelname": "{levelname}", "logger": "{name}", "funcName": "{funcName}", "pathname": "{pathname}", "lineno": {lineno}, "message": {message}}}',
    'handlers': [type("H",(logging.Handler,),{"emit": lambda self,r: sys.stderr.write(f'{self.format(logging.makeLogRecord({**r.__dict__, "msg": json.dumps(r.msg)}))}\n')})()],
    'style': '{',
})
logging.getLogger("uvicorn").handlers = logging.getLogger().handlers
logging.getLogger("uvicorn.access").handlers = logging.getLogger().handlers
