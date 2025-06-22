"""OpenTofuWrapper class for managing OpenTofu operations."""

import subprocess
import os
from accessify import protected, private
from typing import Optional, List

from ..util.logging import logged
from ..schema.tofu_schemas import OpenTofuBackendOptions


@logged
class OpenTofuWrapper:
    """Class for wrapper OpenTofu handles"""

    def __init__(
        self,
        backend_options: OpenTofuBackendOptions,
        working_dir: str,
        tofu_bin: str = "tofu",
    ):
        self.working_dir = os.path.abspath(working_dir)
        self.tofu_bin = tofu_bin
        self.backend_options = backend_options

    @private
    def __run(
        self, args: List[str], capture_output: bool = False
    ) -> subprocess.CompletedProcess:
        cmd = [self.tofu_bin] + args
        return subprocess.run(
            cmd,
            cwd=self.working_dir,
            check=True,
            capture_output=capture_output,
            text=True,
        )

    @protected
    def _init(self) -> None:
        self.backend_options
        self.__run(["init"])

    @protected
    def _plan(self, out_file: Optional[str] = None) -> str:
        args = ["plan"]
        if out_file:
            args += ["-out", out_file]
            result = self.__run(args, capture_output=True)
        return result.stdout

    @protected
    def _apply(self, plan_file: Optional[str] = None, auto_approve: bool = True) -> str:
        args = ["apply"]
        if plan_file:
            args.append(plan_file)
            if auto_approve:
                args.append("-auto-approve")
                result = self.__run(args, capture_output=True)
        return result.stdout

    @protected
    def _destroy(self, auto_approve: bool = True) -> str:
        args = ["destroy"]
        if auto_approve:
            args.append("-auto-approve")
            result = self.__run(args, capture_output=True)
        return result.stdout
