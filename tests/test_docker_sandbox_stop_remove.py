from agent.integrations.docker import DockerSandbox


class _FakeContainer:
    def __init__(self):
        self.short_id = "fake-container-stop"
        self.stopped = False
        self.removed = False

    def reload(self):
        return None

    def stop(self, timeout=5):
        self.stopped = True

    def remove(self, force=True):
        self.removed = True


class _FakeContainerNotFound:
    def __init__(self):
        self.short_id = "fake-container-gone"

    def reload(self):
        return None

    def stop(self, timeout=5):
        from docker.errors import NotFound

        raise NotFound("not found")

    def remove(self, force=True):
        from docker.errors import NotFound

        raise NotFound("not found")


def _make_sandbox(fake_container=None):
    if fake_container is None:
        fake_container = _FakeContainer()
    return DockerSandbox(fake_container), fake_container


def test_stop():
    sandbox, container = _make_sandbox()
    sandbox.stop(timeout=5)
    assert container.stopped is True


def test_stop_already_gone():
    sandbox, _ = _make_sandbox(_FakeContainerNotFound())
    sandbox.stop(timeout=5)


def test_remove():
    sandbox, container = _make_sandbox()
    sandbox.remove(force=True)
    assert container.removed is True


def test_remove_already_gone():
    sandbox, _ = _make_sandbox(_FakeContainerNotFound())
    sandbox.remove(force=True)
