import json
import math
from datetime import datetime
from typing import List

import boto3
from botocore.exceptions import ClientError
from langchain.schema import BaseChatMessageHistory
from langchain.schema.messages import (
    BaseMessage,
)

from .constant import MessageType

client = boto3.resource("dynamodb")


class DynamoDBChatMessageHistory(BaseChatMessageHistory):
    def __init__(
        self,
        sessions_table_name: str,
        messages_table_name: str,
        session_id: str,
        user_id: str,
        client_type: str,
    ):
        self.sessions_table = client.Table(sessions_table_name)
        self.messages_table = client.Table(messages_table_name)
        self.session_id = session_id
        self.user_id = user_id
        self.client_type = client_type
        self.MESSAGE_BY_SESSION_ID_INDEX_NAME = "bySessionId"

    @property
    def session(self):
        response = self.sessions_table.get_item(
            Key={"sessionId": self.session_id, "userId": self.user_id}
        )
        item = response.get("Item")
        return item

    @property
    def messages(self):
        """Retrieve the messages from DynamoDB"""
        response = {}
        try:
            response = self.messages_table.query(
                KeyConditionExpression="sessionId = :session_id",
                ExpressionAttributeValues={":session_id": self.session_id},
                IndexName=self.MESSAGE_BY_SESSION_ID_INDEX_NAME,
            )
        except ClientError as error:
            if error.response["Error"]["Code"] == "ResourceNotFoundException":
                print("No record found for session id: %s", self.session_id)
            else:
                print(error)

        items = response.get("Items", [])
        items = sorted(items, key=lambda x: x["createTimestamp"])

        return items

    @property
    def messages_as_langchain(self):
        response = {}
        try:
            response = self.messages_table.query(
                KeyConditionExpression="sessionId = :session_id",
                ExpressionAttributeValues={":session_id": self.session_id},
                IndexName=self.MESSAGE_BY_SESSION_ID_INDEX_NAME,
            )
        except ClientError as error:
            if error.response["Error"]["Code"] == "ResourceNotFoundException":
                print("No record found for session id: %s", self.session_id)
            else:
                print(error)
        items = response.get("Items", [])
        items = sorted(items, key=lambda x: x["createTimestamp"])
        ret = []

        for item in items:
            assert item["role"] in [
                MessageType.AI_MESSAGE_TYPE,
                MessageType.HUMAN_MESSAGE_TYPE,
            ]
            if item["role"] == MessageType.AI_MESSAGE_TYPE:
                role = "ai"
            else:
                role = "user"
            additional_kwargs = json.loads(item["additional_kwargs"])
            langchain_message_template = {
                "role": role,
                "content": item["content"],
                "additional_kwargs": {
                    "message_id": item["messageId"],
                    "create_time": item["createTimestamp"],
                    "entry_type": item["entryType"],
                    "custom_message_id": item["customMessageId"],
                    **additional_kwargs,
                },
            }
            ret.append(langchain_message_template)
        return ret

    def update_session(self):
        """Add the session to the record in DynamoDB"""
        session = self.session
        # If this session already exists, update lastModifiedTimestamp
        if session:
            current_timestamp = datetime.utcnow().isoformat() + "Z"
            response = self.sessions_table.update_item(
                Key={"sessionId": self.session_id, "userId": self.user_id},
                UpdateExpression="SET lastModifiedTimestamp = :t",
                ExpressionAttributeValues={":t": current_timestamp},
                ReturnValues="UPDATED_NEW",
            )
        else:
            current_timestamp = datetime.utcnow().isoformat() + "Z"
            response = self.sessions_table.put_item(
                Item={
                    "sessionId": self.session_id,
                    "userId": self.user_id,
                    "clientType": self.client_type,
                    "startTime": current_timestamp,
                    "createTimestamp": current_timestamp,
                    "lastModifiedTimestamp": current_timestamp,
                }
            )

    def add_message(
        self,
        message_id,
        message_type,
        custom_message_id,
        entry_type,
        message_content,
        input_message_id="",
        additional_kwargs=None,
    ) -> None:
        """Append the message to the record in DynamoDB"""
        current_timestamp = datetime.utcnow().isoformat() + "Z"
        additional_kwargs = additional_kwargs or {}

        try:
            response = self.messages_table.put_item(
                Item={
                    "messageId": message_id,
                    "sessionId": self.session_id,
                    "role": message_type,
                    "customMessageId": custom_message_id,
                    "inputMessageId": input_message_id,
                    "entryType": entry_type,
                    "content": message_content,
                    "createTimestamp": current_timestamp,
                    "lastModifiedTimestamp": current_timestamp,
                    "additional_kwargs": json.dumps(additional_kwargs),
                }
            )
        except ClientError as err:
            print(f"Error adding message: {err}")

    def add_user_message(
        self,
        message_id,
        custom_message_id,
        entry_type,
        message_content,
        additional_kwargs=None,
    ) -> None:
        """Append the user message to the record in DynamoDB"""
        self.add_message(
            message_id,
            MessageType.HUMAN_MESSAGE_TYPE,
            custom_message_id,
            entry_type,
            message_content,
            additional_kwargs=additional_kwargs,
        )
        self.update_session()

    def add_ai_message(
        self,
        message_id,
        custom_message_id,
        entry_type,
        message_content,
        input_message_id,
        additional_kwargs=None,
    ) -> None:
        """Append the ai message to the record in DynamoDB"""
        self.add_message(
            message_id,
            MessageType.AI_MESSAGE_TYPE,
            custom_message_id,
            entry_type,
            message_content,
            input_message_id,
            additional_kwargs=additional_kwargs,
        )
        self.update_session()

    def clear(self) -> None:
        """Clear session memory from DynamoDB"""
        try:
            self.messages_table.delete_item(
                Key={"sessionId": self.session_id, "messageId": self.message_id}
            )
        except ClientError as err:
            print(err)


def filter_chat_history_by_time(
    chat_history: List[BaseMessage], start_time=-math.inf, end_time=math.inf
):
    chat_history = sorted(
        chat_history, key=lambda x: x.additional_kwargs["create_time"]
    )
    selected_indexes = []
    for i, message in enumerate(chat_history):
        create_time = message.additional_kwargs["create_time"]
        if start_time <= create_time <= end_time:
            selected_indexes.append(i)

    # deal with boundry condition
    if selected_indexes:
        start_index = selected_indexes[0]
        end_index = selected_indexes[-1]

        if (
            chat_history[start_index].type == MessageType.AI_MESSAGE_TYPE
            and start_index != 0
        ):
            selected_indexes.insert(0, start_index - 1)

        if chat_history[
            end_index
        ].type == MessageType.HUMAN_MESSAGE_TYPE and end_index != (
            len(chat_history) - 1
        ):
            selected_indexes.append(end_index + 1)
    ret = [chat_history[i] for i in selected_indexes]
    return ret
