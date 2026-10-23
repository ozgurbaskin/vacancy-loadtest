import datetime

from google.protobuf import timestamp_pb2 as _timestamp_pb2
from google.protobuf.internal import enum_type_wrapper as _enum_type_wrapper
from google.protobuf import descriptor as _descriptor
from google.protobuf import message as _message
from collections.abc import Mapping as _Mapping
from typing import ClassVar as _ClassVar, Optional as _Optional, Union as _Union

DESCRIPTOR: _descriptor.FileDescriptor

class Vacancy(_message.Message):
    __slots__ = ("Id", "Title", "Description", "Views", "Division", "Country", "created_at", "updated_at")
    class DIVISION(int, metaclass=_enum_type_wrapper.EnumTypeWrapper):
        __slots__ = ()
        DEVELOPMENT: _ClassVar[Vacancy.DIVISION]
        SECURITY: _ClassVar[Vacancy.DIVISION]
        SALES: _ClassVar[Vacancy.DIVISION]
        OTHER: _ClassVar[Vacancy.DIVISION]
    DEVELOPMENT: Vacancy.DIVISION
    SECURITY: Vacancy.DIVISION
    SALES: Vacancy.DIVISION
    OTHER: Vacancy.DIVISION
    ID_FIELD_NUMBER: _ClassVar[int]
    TITLE_FIELD_NUMBER: _ClassVar[int]
    DESCRIPTION_FIELD_NUMBER: _ClassVar[int]
    VIEWS_FIELD_NUMBER: _ClassVar[int]
    DIVISION_FIELD_NUMBER: _ClassVar[int]
    COUNTRY_FIELD_NUMBER: _ClassVar[int]
    CREATED_AT_FIELD_NUMBER: _ClassVar[int]
    UPDATED_AT_FIELD_NUMBER: _ClassVar[int]
    Id: str
    Title: str
    Description: str
    Views: int
    Division: Vacancy.DIVISION
    Country: str
    created_at: _timestamp_pb2.Timestamp
    updated_at: _timestamp_pb2.Timestamp
    def __init__(self, Id: _Optional[str] = ..., Title: _Optional[str] = ..., Description: _Optional[str] = ..., Views: _Optional[int] = ..., Division: _Optional[_Union[Vacancy.DIVISION, str]] = ..., Country: _Optional[str] = ..., created_at: _Optional[_Union[datetime.datetime, _timestamp_pb2.Timestamp, _Mapping]] = ..., updated_at: _Optional[_Union[datetime.datetime, _timestamp_pb2.Timestamp, _Mapping]] = ...) -> None: ...

class VacancyResponse(_message.Message):
    __slots__ = ("vacancy",)
    VACANCY_FIELD_NUMBER: _ClassVar[int]
    vacancy: Vacancy
    def __init__(self, vacancy: _Optional[_Union[Vacancy, _Mapping]] = ...) -> None: ...
