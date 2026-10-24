from wishlist.notify import DeliveryError


class RecordingMailer:
    def __init__(self):
        self.sent: list[tuple[str, str, str]] = []

    def send(self, to, subject, body):
        self.sent.append((to, subject, body))


class RecordingSms:
    def __init__(self):
        self.sent: list[tuple[str, str]] = []

    def send(self, to, text):
        self.sent.append((to, text))


class FailingMailer:
    def send(self, to, subject, body):
        raise DeliveryError("mail down")


class FailingSms:
    def send(self, to, text):
        raise DeliveryError("sms down")
