"""Connecting the mailbox from the product, and the refusals that make it trustworthy.

Until Phase 8 the only way to register the Gmail watch was `tools/gmail_watch.py --apply`,
and that string was printed **inside the interface** as an instruction to the reader. These
tests pin the behaviour that replaced it, and every one of them is about a refusal rather
than about the happy path — because the happy path here is one API call and the interesting
part is everything the code declines to do.

The property that matters most: **`users.watch` succeeds against a topic nobody is
subscribed to.** It returns a history id and an expiry, Firestore records a healthy-looking
registration, and every notification Gmail publishes falls into a void for seven days. That
is the worst outcome available, because it looks exactly like it worked, and it is the
reason `register` checks before it registers.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from attestor_core.errors import ConfigurationError
from attestor_platform.gmail import watch as watch_module
from attestor_platform.gmail.watch import (
    TopicCheck,
    WatchRefused,
    register,
    status,
    stop,
)


class FakeState:
    """The Firestore cursor, in memory."""

    def __init__(self, cursor: dict[str, Any] | None = None) -> None:
        self._cursor = dict(cursor or {})

    def cursor(self) -> dict[str, Any]:
        return dict(self._cursor)

    def record_watch(
        self, history_id: str, expiration_ms: int, topic: str, address: str = ""
    ) -> None:
        self._cursor = {
            "history_id": history_id,
            "expiration_ms": expiration_ms,
            "topic": topic,
            "address": address,
            "registered_at": datetime.now(UTC).isoformat(),
        }


class Registration:
    """What `users.watch` returns, as far as this module cares."""

    def __init__(self, topic: str) -> None:
        self.history_id = "9001"
        self.expiration_ms = int((datetime.now(UTC) + timedelta(days=7)).timestamp() * 1000)
        self.topic = topic


class Mailbox:
    """The mailbox. Records what it was asked to do and never touches a network."""

    def __init__(self, address: str = "attestor.trust@example.com") -> None:
        self.address = address
        self.watched: list[str] = []
        self.scoped_to: list[tuple[str, ...]] = []
        self.labels_ensured: list[str] = []
        self.stopped = 0

    def ensure_label(self, name: str) -> str:
        self.labels_ensured.append(name)
        return f"Label_{name}"

    def watch(self, topic: str, label_ids: tuple[str, ...] = ("INBOX",)) -> Registration:
        self.watched.append(topic)
        self.scoped_to.append(label_ids)
        return Registration(topic)

    def stop_watch(self) -> None:
        self.stopped += 1


def consent(monkeypatch: pytest.MonkeyPatch, *, granted: bool) -> None:
    monkeypatch.setattr(watch_module, "has_consent", lambda: granted)


def topic(monkeypatch: pytest.MonkeyPatch, check: TopicCheck) -> None:
    monkeypatch.setattr(watch_module, "check_topic", lambda project, name: check)


DELIVERABLE = TopicCheck(
    exists=True,
    publisher_bound=True,
    subscriptions=("projects/p/subscriptions/attestor.gmail.push",),
    note="deliverable",
)


class TestRegisteringRefusesRatherThanLying:
    def test_a_topic_nobody_subscribes_to_is_refused(self, monkeypatch: pytest.MonkeyPatch) -> None:
        consent(monkeypatch, granted=True)
        topic(
            monkeypatch,
            TopicCheck(
                exists=True,
                publisher_bound=True,
                subscriptions=(),
                note="Nothing is subscribed to projects/p/topics/t.",
            ),
        )
        mailbox = Mailbox()
        with pytest.raises(WatchRefused, match="Nothing is subscribed"):
            register(project="p", topic="t", gmail=mailbox, state=FakeState())
        # The important half: nothing was registered.
        assert mailbox.watched == []

    def test_a_topic_gmail_cannot_publish_to_is_refused(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        consent(monkeypatch, granted=True)
        topic(
            monkeypatch,
            TopicCheck(exists=True, publisher_bound=False, subscriptions=(), note="not permitted"),
        )
        mailbox = Mailbox()
        with pytest.raises(WatchRefused, match="not permitted"):
            register(project="p", topic="t", gmail=mailbox, state=FakeState())
        assert mailbox.watched == []

    def test_a_missing_topic_is_refused(self, monkeypatch: pytest.MonkeyPatch) -> None:
        consent(monkeypatch, granted=True)
        topic(
            monkeypatch,
            TopicCheck(exists=False, publisher_bound=False, note="The topic does not exist."),
        )
        with pytest.raises(WatchRefused, match="does not exist"):
            register(project="p", topic="t", gmail=Mailbox(), state=FakeState())

    def test_no_consent_is_a_refusal_and_not_a_crash(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """This reached a person as `500 Internal Server Error` before the check existed.

        A fresh deployment has no consent document, which is a state somebody can act on --
        sign in, approve the scopes -- and rendering it as an unhandled server error turns a
        fixable state into an unfixable one.
        """
        consent(monkeypatch, granted=False)
        topic(monkeypatch, DELIVERABLE)
        mailbox = Mailbox()
        with pytest.raises(WatchRefused, match="No mailbox has granted"):
            register(project="p", topic="t", gmail=mailbox, state=FakeState())
        assert mailbox.watched == []

    def test_no_project_is_refused_before_anything_else(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("PROJECT_ID", raising=False)
        with pytest.raises(WatchRefused, match="No Google Cloud project"):
            register(project="", gmail=Mailbox(), state=FakeState())


class TestTheWatchSeesOneLabelAndNotAMailbox:
    """The privacy claim this integration makes, held by a test rather than by a comment.

    Attestor watches a mailbox a person also uses for their own mail. A watch on INBOX
    publishes a notification for every message that mailbox receives, and every one of them
    would be fetched, parsed and judged by this deployment before being discarded. Scoped to
    a label, the mailbox owner decides with a Gmail filter what this system is ever told
    about -- in their own client, revocable without touching the deployment.

    The failure this guards against is a default. `GmailClient.watch` defaults to
    `("INBOX",)`, so a `register()` that stopped passing a label would keep working, keep
    delivering mail, and quietly widen the scope to everything. Nothing else would break.
    """

    def test_the_watch_is_scoped_to_the_label_and_never_to_the_inbox(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        consent(monkeypatch, granted=True)
        topic(monkeypatch, DELIVERABLE)
        mailbox = Mailbox()

        register(project="p", topic="t", label="Attestor", gmail=mailbox, state=FakeState())

        assert mailbox.scoped_to == [("Label_Attestor",)]
        assert "INBOX" not in mailbox.scoped_to[0]

    def test_the_label_is_created_so_the_owner_only_has_to_write_the_filter(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        consent(monkeypatch, granted=True)
        topic(monkeypatch, DELIVERABLE)
        mailbox = Mailbox()

        register(project="p", topic="t", label="Attestor", gmail=mailbox, state=FakeState())

        assert mailbox.labels_ensured == ["Attestor"]


class TestARegistrationIsRecordedWhereTheProductReadsIt:
    def test_registering_stores_the_mailbox_beside_the_cursor(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """So a service with no Gmail credential can still name the watched mailbox."""
        consent(monkeypatch, granted=True)
        topic(monkeypatch, DELIVERABLE)
        state = FakeState()
        mailbox = Mailbox()

        result = register(project="p", topic="t", gmail=mailbox, state=state)

        assert mailbox.watched == ["projects/p/topics/t"]
        assert result.connected is True
        assert result.address == "attestor.trust@example.com"
        assert state.cursor()["address"] == "attestor.trust@example.com"
        assert state.cursor()["history_id"] == "9001"

    def test_the_scopes_are_reported_with_what_each_one_permits(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """`drive.file` is the least-privilege story, and it only lands if it is legible."""
        consent(monkeypatch, granted=True)
        topic(monkeypatch, DELIVERABLE)
        payload = register(project="p", topic="t", gmail=Mailbox(), state=FakeState()).as_dict()
        grants = {row["scope"]: row["grants"] for row in payload["scopes"]}
        assert len(grants) == 4
        assert (
            "only the files this application creates"
            in grants["https://www.googleapis.com/auth/drive.file"]
        )
        assert all(sentence for sentence in grants.values())


class TestStoppingLeavesNothingClaimingToBeWatched:
    def test_stopping_clears_the_recorded_expiry(self) -> None:
        """A stopped watch whose record still reads "expires in 140h" is the same class of
        lie as an unperformed check rendering as a passed one."""
        expiry = int((datetime.now(UTC) + timedelta(days=6)).timestamp() * 1000)
        state = FakeState(
            {"expiration_ms": expiry, "address": "a@b.c", "topic": "t", "history_id": "1"}
        )
        mailbox = Mailbox()

        result = stop(gmail=mailbox, state=state)

        assert mailbox.stopped == 1
        assert result.connected is False
        assert state.cursor()["expiration_ms"] == 0
        assert status(state=state).connected is False


class TestStatusReadsFirestoreAndNothingElse:
    def test_an_expired_watch_reports_expired_rather_than_connected(self) -> None:
        past = int((datetime.now(UTC) - timedelta(hours=3)).timestamp() * 1000)
        state = FakeState({"expiration_ms": past, "address": "a@b.c"})
        result = status(state=state)
        assert result.connected is False
        assert result.expired is True
        assert result.expires_in_hours is not None and result.expires_in_hours < 0

    def test_a_live_watch_reports_the_hours_remaining(self) -> None:
        soon = int((datetime.now(UTC) + timedelta(hours=48)).timestamp() * 1000)
        result = status(state=FakeState({"expiration_ms": soon, "address": "a@b.c"}))
        assert result.connected is True
        assert result.expired is False
        assert 47 <= (result.expires_in_hours or 0) <= 48

    def test_a_mailbox_that_was_never_connected_says_so(self) -> None:
        assert status(state=FakeState()).connected is False


class TestConsentIsABooleanAndNotAnException:
    def test_a_missing_secret_reads_as_no_consent(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def explode() -> dict[str, Any]:
            raise ConfigurationError("secret not found")

        monkeypatch.setattr("attestor_platform.gmail.client.oauth_payload", explode)
        assert watch_module.has_consent() is False

    def test_a_present_secret_reads_as_consent(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            "attestor_platform.gmail.client.oauth_payload",
            lambda: {"client_id": "a", "client_secret": "b", "refresh_token": "c"},
        )
        assert watch_module.has_consent() is True


class TestAnUnreadablePolicyIsNotABrokenTopic:
    """Measured on the deployed service: `pubsub.topics.getIamPolicy` is not in
    `roles/pubsub.viewer`, so the same check that passed under a developer's own credentials
    came back 403 in Cloud Run. Reporting a healthy topic as broken because a *status read*
    was refused is the failure-impersonating-empty shape, and it would have blocked Connect
    on a deployment where everything was fine."""

    def test_an_unchecked_binding_does_not_block_registration(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        consent(monkeypatch, granted=True)
        topic(
            monkeypatch,
            TopicCheck(
                exists=True,
                publisher_bound=False,
                publisher_checked=False,
                subscriptions=("projects/p/subscriptions/s",),
                note="cannot be checked from this service",
            ),
        )
        mailbox = Mailbox()
        result = register(project="p", topic="t", gmail=mailbox, state=FakeState())
        assert mailbox.watched == ["projects/p/topics/t"]
        assert result.connected is True

    def test_a_binding_that_was_read_and_is_absent_still_blocks(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The difference that matters: unknown is not the same as known-missing."""
        consent(monkeypatch, granted=True)
        topic(
            monkeypatch,
            TopicCheck(
                exists=True,
                publisher_bound=False,
                publisher_checked=True,
                subscriptions=("projects/p/subscriptions/s",),
                note="Gmail's publisher identity is not permitted to publish",
            ),
        )
        mailbox = Mailbox()
        with pytest.raises(WatchRefused, match="not permitted to publish"):
            register(project="p", topic="t", gmail=mailbox, state=FakeState())
        assert mailbox.watched == []

    def test_no_subscribers_blocks_however_the_binding_reads(self) -> None:
        assert (
            TopicCheck(
                exists=True, publisher_bound=True, publisher_checked=False, subscriptions=()
            ).deliverable
            is False
        )

    def test_gmails_own_refusal_is_relayed_verbatim(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Gmail is the authority on whether it may publish, and its 4xx names the topic."""
        from attestor_core.errors import ContextUnavailable

        consent(monkeypatch, granted=True)
        topic(monkeypatch, DELIVERABLE)

        class Refusing(Mailbox):
            def watch(self, topic: str, label_ids: tuple[str, ...] = ("INBOX",)) -> Registration:
                raise ContextUnavailable(
                    "gmail POST /watch -> 403: User not authorized to perform this action.",
                    status_code=403,
                )

        with pytest.raises(WatchRefused, match="User not authorized"):
            register(project="p", topic="t", gmail=Refusing(), state=FakeState())
