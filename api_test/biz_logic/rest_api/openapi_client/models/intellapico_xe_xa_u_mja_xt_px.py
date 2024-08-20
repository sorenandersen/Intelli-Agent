# coding: utf-8

"""
    Intelli-Agent-RESTful-API

    Intelli-Agent RESTful API

    The version of the OpenAPI document: 2024-07-29T06:23:46Z
    Generated by OpenAPI Generator (https://openapi-generator.tech)

    Do not edit the class manually.
"""  # noqa: E501


from __future__ import annotations
import pprint
import re  # noqa: F401
import json

from pydantic import BaseModel, ConfigDict, Field, StrictStr
from typing import Any, ClassVar, Dict, List, Optional
from typing import Optional, Set
from typing_extensions import Self

class IntellapicoXeXaUMjaXtPx(BaseModel):
    """
    IntellapicoXeXaUMjaXtPx
    """ # noqa: E501
    data: Optional[StrictStr] = None
    message: Optional[StrictStr] = None
    s3_prefix: Optional[StrictStr] = Field(default=None, alias="s3Prefix")
    s3_bucket: Optional[StrictStr] = Field(default=None, alias="s3Bucket")
    __properties: ClassVar[List[str]] = ["data", "message", "s3Prefix", "s3Bucket"]

    model_config = ConfigDict(
        populate_by_name=True,
        validate_assignment=True,
        protected_namespaces=(),
    )


    def to_str(self) -> str:
        """Returns the string representation of the model using alias"""
        return pprint.pformat(self.model_dump(by_alias=True))

    def to_json(self) -> str:
        """Returns the JSON representation of the model using alias"""
        # TODO: pydantic v2: use .model_dump_json(by_alias=True, exclude_unset=True) instead
        return json.dumps(self.to_dict())

    @classmethod
    def from_json(cls, json_str: str) -> Optional[Self]:
        """Create an instance of IntellapicoXeXaUMjaXtPx from a JSON string"""
        return cls.from_dict(json.loads(json_str))

    def to_dict(self) -> Dict[str, Any]:
        """Return the dictionary representation of the model using alias.

        This has the following differences from calling pydantic's
        `self.model_dump(by_alias=True)`:

        * `None` is only added to the output dict for nullable fields that
          were set at model initialization. Other fields with value `None`
          are ignored.
        """
        excluded_fields: Set[str] = set([
        ])

        _dict = self.model_dump(
            by_alias=True,
            exclude=excluded_fields,
            exclude_none=True,
        )
        return _dict

    @classmethod
    def from_dict(cls, obj: Optional[Dict[str, Any]]) -> Optional[Self]:
        """Create an instance of IntellapicoXeXaUMjaXtPx from a dict"""
        if obj is None:
            return None

        if not isinstance(obj, dict):
            return cls.model_validate(obj)

        _obj = cls.model_validate({
            "data": obj.get("data"),
            "message": obj.get("message"),
            "s3Prefix": obj.get("s3Prefix"),
            "s3Bucket": obj.get("s3Bucket")
        })
        return _obj

