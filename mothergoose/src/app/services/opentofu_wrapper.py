"""OpenTofuWrapper class for managing OpenTofu operations."""

import os
import subprocess
from typing import Any, List, Optional

from accessify import private

from app.schema.tofu_schemas import OpenTofuBackendOptions
from app.util.logging import logged


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

    def init(self) -> None:
        """Initialize the OpenTofu working directory."""
        self.__run(["init"])

    def plan(self, out_file: Optional[str] = None) -> str:
        """Create an OpenTofu plan."""
        result = Any
        args = ["plan"]
        if out_file:
            args += ["-out", out_file]
            result = self.__run(args, capture_output=True)
        return result.stdout

    def apply(
        self,
        plan_file: Optional[str] = None,
        auto_approve: bool = True,
    ) -> str:
        """Apply the OpenTofu plan."""
        result = Any
        args = ["apply"]
        if plan_file:
            args.append(plan_file)
            if auto_approve:
                args.append("-auto-approve")
                result = self.__run(args, capture_output=True)
        return result.stdout

    def destroy(self, auto_approve: bool = True) -> str:
        """Destroy the OpenTofu managed infrastructure."""
        result = Any
        args = ["destroy"]
        if auto_approve:
            args.append("-auto-approve")
            result = self.__run(args, capture_output=True)
        return result.stdout

    @private
    def __run(
        self, args: List[str], capture_output: bool = False
    ) -> subprocess.CompletedProcess:
        """Run the OpenTofu command with the given arguments."""

        cmd = [self.tofu_bin] + args
        return subprocess.run(
            cmd,
            cwd=self.working_dir,
            check=True,
            capture_output=capture_output,
            text=True,
        )
