from . import vacancy_pb2 as _vacancy_pb2
from google.protobuf import descriptor as _descriptor
from google.protobuf import message as _message
from typing import ClassVar as _ClassVar, Optional as _Optional, Union as _Union

DESCRIPTOR: _descriptor.FileDescriptor

class CreateVacancyRequest(_message.Message):
    __slots__ = ("Title", "Description", "Division", "Country")
    TITLE_FIELD_NUMBER: _ClassVar[int]
    DESCRIPTION_FIELD_NUMBER: _ClassVar[int]
    DIVISION_FIELD_NUMBER: _ClassVar[int]
    COUNTRY_FIELD_NUMBER: _ClassVar[int]
    Title: str
    Description: str
    Division: _vacancy_pb2.Vacancy.DIVISION
    Country: str
    def __init__(self, Title: _Optional[str] = ..., Description: _Optional[str] = ..., Division: _Optional[_Union[_vacancy_pb2.Vacancy.DIVISION, str]] = ..., Country: _Optional[str] = ...) -> None: ...
