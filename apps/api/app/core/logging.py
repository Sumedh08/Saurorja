import logging
from contextvars import ContextVar

from pythonjsonlogger.json import JsonFormatter

request_id_context: ContextVar[str] = ContextVar("request_id", default="-")


class ContextFilter(logging.Filter):
    def __init__(self, service: str, environment: str) -> None:
        super().__init__()
        self.service = service
        self.environment = environment

    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = request_id_context.get()
        record.service = self.service
        record.environment = self.environment
        return True


def configure_logging(level: str, service: str, environment: str) -> None:
    handler = logging.StreamHandler()
    handler.addFilter(ContextFilter(service, environment))
    handler.setFormatter(
        JsonFormatter(
            "%(asctime)s %(levelname)s %(service)s %(environment)s %(request_id)s %(message)s"
        )
    )
    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(level)
