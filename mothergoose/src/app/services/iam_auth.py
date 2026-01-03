"""
Yandex Cloud IAM Authentication Helper

Provides reusable authentication for Yandex Cloud services.
Handles token and folder_id retrieval from magic link or static configuration.
"""

import json
import os
import sys
import requests
from typing import Literal, Union

from app.schema.cloud_connections_schemas import YandexCloudConnectionInfo
from app.util.base_logging import logged
from app.util.class_access import only_called_by
from app.util.exceptions import AbsentReplyError


@logged
class YandexCloudIAMAuth:
    """Class for managing Yandex Cloud IAM Bearer token."""

    # pylint: disable=no-member,too-few-public-methods
    def __init__(self, yc_connection_info: YandexCloudConnectionInfo) -> None:
        self.iam_token = yc_connection_info.yc_token
        self.folder_id = yc_connection_info.folder_id
        self.server_api = yc_connection_info.server_api

    def __retrieving_data(
        self,
        data_type: Union[Literal["iam", "folder_id"]],
    ) -> str:
        """Private method to retrieve data from metadata server"""

        if data_type == "folder_id":
            data = self.folder_id
            url = f"{self.server_api}/vendor/folder-id"
        else:
            data = self.iam_token
            url = f"{self.server_api}/service-accounts/default/token"

        if data is None:
            headers = {"Metadata-Flavor": "Google"}
            try:
                resp = requests.get(url, headers=headers, timeout=5)
                raw = resp.content.decode("UTF-8")
                if data_type == "folder_id":
                    data = raw

                else:
                    data = json.loads(raw)["access_token"]
                response = data
            except (
                KeyError,
                json.JSONDecodeError,
                requests.exceptions.ConnectionError,
            ):
                if os.environ.get("PY_TEST") == "True":
                    print("Magic link data is not exist!")
                    response = "Empty"
                else:
                    self.error("Magic link data is not exist!")
                    raise AbsentReplyError("Magic link data is not exist!")
            else:
                self.info("Token was retrieved successfully.")
        else:
            self.info("Token was set statically.")
            response = data
        return str(response)

    @only_called_by("YandexCloudTriggerManager")
    def get_token_serverless(self) -> str:
        """Method to get IAM Bearer token in serverless"""

        return self.__retrieving_data(data_type="iam")

    @only_called_by("YandexCloudTriggerManager")
    def get_folder_id_serverless(self) -> str:
        """Method to get folder_id in serverless"""

        return self.__retrieving_data(data_type="folder_id")
