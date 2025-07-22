from fastapi import FastAPI, HTTPException

from .schema.payload_schemas import OpenTofuPayload
from .services.download_and_update_opentofu_binary import *
from .services.opentofu_wrapper import *
from .util.logging import logged

# class AppCreator:
#     """App Creator class wrapper for MotherGoose"""
#
#     def __init__(self):
#         self.run_opentofu = OpenTofuWrapper()
#
#     def run_opentofu_service(self, config: str):
#         return self.run_opentofu(config)


# app = FastAPI()
# app_creator = AppCreator()
#
#
# @app.post("/opentofu/run")
# @loggd
# async def run_opentofu_endpoint(payload: OpenTofuPayload):
#     try:
#         result = app_creator.run_opentofu_service(payload.config)
#         return {"result": result}
#     except Exception as e:
#         log.error("Server error code 500. Unexpected request.")
#         raise HTTPException(status_code=500, detail=str(e))
