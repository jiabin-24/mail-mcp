import os
from pathlib import Path

from dotenv import dotenv_values


class EnvironmentBootstrapper:
    """按稳定优先级加载本地 dotenv 文件中的环境变量。"""

    def __init__(self, root_dir: Path) -> None:
        self.root_dir = root_dir

    @staticmethod
    def _load_env_file(path: Path) -> None:
        if not path.exists():
            print(f"[env-bootstrap] skip missing env file: {path}")
            return

        # 按优先级加载本地环境变量：只在当前进程中未显式设置时写入默认值。
        loaded_keys = 0
        for key, value in dotenv_values(path).items():
            if value is None:
                continue
            # 进程级环境变量优先级最高，适用于 App Service / Secret 配置等场景。
            os.environ.setdefault(key, value)
            loaded_keys += 1

        print(f"[env-bootstrap] loaded env file: {path} keys={loaded_keys}")

    def bootstrap(self) -> None:
        # APP_ENV 允许按环境切换加载 .env / .env.{APP_ENV} / .env.prod。
        app_env = os.getenv("APP_ENV", "").strip().lower()
        env_files: list[Path] = [self.root_dir / ".env"]

        if app_env:
            env_files.append(self.root_dir / f".env.{app_env}")
        else:
            env_files.append(self.root_dir / ".env.prod")

        for env_file in env_files:
            self._load_env_file(env_file)
