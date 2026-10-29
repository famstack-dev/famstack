"""The model at its boundary, for any test instance: the chat completions API.

A command that asks the model reaches it over HTTP, so a test stands it in
on a pytest-httpserver and names its `url` where the instance's commands
look for the model (memory's `running_memory(model=...)`, an extension's
`stack.uses_model(...)`):

    model = Model(httpserver)
    model.answers({"title": "Tent for four"}, "A plain sentence.")
    ...
    assert "tent" in model.requests[-1][-1]["content"]

It is the model, not a mock of the client: the real `stack.ai.client`
sends the request and reads the answer.
"""

from __future__ import annotations

import json
from collections.abc import Callable


class Model:
    """The model at its boundary: the chat completions API on a
    pytest-httpserver, as the bot runner's commands reach it at `url`.

    It answers what a test gives (`answers`), in order, and the last one
    again after that: a dict as its JSON, a string as it is. Or it answers
    what `answering` makes of each request. It keeps every request's
    messages (`requests`), the newest last. With `usage` set (`{}`
    included), every answer carries it as the response's `usage` block, as
    a server that counts tokens sends it; with None, the answer has none.
    `refuses(status)` makes every request fail with that HTTP status, as a
    server refuses a prompt over its context (400).
    """

    def __init__(self, httpserver):
        from werkzeug.wrappers import Response

        self.url = httpserver.url_for("/v1")
        self.requests: list[list[dict]] = []
        self._answers: list[dict | str] = [{}]
        self._from = 0
        self._by: Callable[[list[dict]], str] | None = None
        self.usage: dict | None = None
        self._status = 200

        def complete(request):
            messages = json.loads(request.data)["messages"]
            self.requests.append(messages)
            if self._status != 200:
                return Response(json.dumps({"error": {"message": "refused", "type": "invalid_request_error"}}),
                                status=self._status, content_type="application/json")
            if self._by is not None:
                content = self._by(messages)
            else:
                answer = self._answers[min(len(self.requests) - self._from, len(self._answers)) - 1]
                content = answer if isinstance(answer, str) else json.dumps(answer)
            return Response(json.dumps({
                "id": "c1", "object": "chat.completion", "created": 0, "model": "stand-in",
                "choices": [{"index": 0, "finish_reason": "stop",
                             "message": {"role": "assistant", "content": content}}],
                **({"usage": self.usage} if self.usage is not None else {}),
            }), content_type="application/json")

        httpserver.expect_request("/v1/chat/completions", method="POST").respond_with_handler(complete)

    def answers(self, *answers: dict | str) -> None:
        """What the next requests are answered, in order."""
        self._answers, self._from, self._by = list(answers), len(self.requests), None

    def answering(self, by: Callable[[list[dict]], str]) -> None:
        """Every request from now on is answered `by(messages)`, as the
        model's text: one stand-in for many kinds of request."""
        self._by = by

    def refuses(self, status: int) -> None:
        """Every next request fails with HTTP `status`."""
        self._status = status
