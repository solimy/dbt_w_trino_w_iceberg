import os
import enum
import logging


logging.getLogger("app.components.code_repository").handlers = logging.getLogger().handlers
logger = logging.getLogger("app.components.code_repository")


class RepositoryType(enum.Enum):
    GITHUB = "github"
    GITLAB = "gitlab"
    BITBUCKET = "bitbucket"
    LOCAL = "local"

    def from_url(url: str):
        if os.path.exists(url):
            return RepositoryType.LOCAL
        else:
            raise ValueError(f"Unknown repository type for url: {url}")


class LocalRepositoryConnector:
    def __init__(self, path: str):
        self.path = path

    def connect(self): ...
    def disconnect(self): ...

    def create(self, name: str, description: str, fork: str = None):
        repo_path = os.path.join(self.path, name)
        if fork:
            fork_path = os.path.join(self.path, fork)
            if not os.path.exists(fork_path):
                raise FileNotFoundError(f"Fork template {fork} does not exist at {fork_path}.")
            import shutil
            shutil.copytree(fork_path, repo_path)
            logger.info(f"Forked local repository from {fork_path} to {repo_path}.")
        else:
            os.makedirs(repo_path, exist_ok=True)
            logger.info(f"Created new local repository at {repo_path}.")


class CodeRepository:
    def __init__(self, url: str):
        self.url = url
        self.repo_type = RepositoryType.from_url(url)
        self.connector = None
        if self.repo_type == RepositoryType.LOCAL:
            self.connector = LocalRepositoryConnector(url)

    def connect(self):
        logger.info(f"Connecting to code repository at {self.url}.")
        self.connector.connect()

    def disconnect(self):
        logger.info(f"Disconnecting from code repository at {self.url}.")
        self.connector.disconnect()

    def create_repository(self, name: str, description: str, fork: str = None):
        if fork:
            logger.info(f"Forking repository {fork} to create {name} at {self.url}/{name}.")
        else:
            logger.info(f"Creating repository {name} at {self.url}/{name}.")
        self.connector.create(name, description, fork)

    def __enter__(self):
        self.connect()
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        self.disconnect()