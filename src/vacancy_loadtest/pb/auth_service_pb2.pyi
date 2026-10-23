from . import rpc_signin_user_pb2 as _rpc_signin_user_pb2
from . import rpc_signup_user_pb2 as _rpc_signup_user_pb2
from . import user_pb2 as _user_pb2
from google.protobuf import descriptor as _descriptor
from google.protobuf import message as _message
from typing import ClassVar as _ClassVar, Optional as _Optional

DESCRIPTOR: _descriptor.FileDescriptor

class VerifyEmailRequest(_message.Message):
    __slots__ = ("verificationCode",)
    VERIFICATIONCODE_FIELD_NUMBER: _ClassVar[int]
    verificationCode: str
    def __init__(self, verificationCode: _Optional[str] = ...) -> None: ...
