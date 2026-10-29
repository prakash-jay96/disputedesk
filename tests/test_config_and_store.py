"""Configuration loading and the seller store adapter."""
import json

import pytest

from app import env
from app.store import SellerStore


# --- .env loading -----------------------------------------------------------

def test_parse_handles_comments_exports_quotes_and_blank_lines():
    values = env.parse(
        """
        # a comment
        ANTHROPIC_API_KEY=sk-ant-123

        export MAX_AGENT_TURNS=4
        CLAUDE_MODEL="claude-sonnet-5-5"
        PAYPAL_MODE='sandbox'
        EMPTY=
        NOT_A_PAIR
        """
    )
    assert values == {
        "ANTHROPIC_API_KEY": "sk-ant-123",
        "MAX_AGENT_TURNS": "4",
        "CLAUDE_MODEL": "claude-sonnet-5-5",
        "PAYPAL_MODE": "sandbox",
        "EMPTY": "",
    }


def test_a_value_containing_equals_is_kept_whole():
    assert env.parse("TOKEN=abc=def==")["TOKEN"] == "abc=def=="


def test_real_environment_variables_win_over_the_file(tmp_path, monkeypatch):
    path = tmp_path / ".env"
    path.write_text("CLAUDE_MODEL=from-file\n")
    monkeypatch.setenv("CLAUDE_MODEL", "from-shell")
    env.load(path)
    import os
    assert os.environ["CLAUDE_MODEL"] == "from-shell"


def test_override_replaces_the_environment(tmp_path, monkeypatch):
    path = tmp_path / ".env"
    path.write_text("CLAUDE_MODEL=from-file\n")
    monkeypatch.setenv("CLAUDE_MODEL", "from-shell")
    env.load(path, override=True)
    import os
    assert os.environ["CLAUDE_MODEL"] == "from-file"


def test_a_missing_env_file_is_not_an_error(tmp_path):
    assert env.load(tmp_path / "nope.env") == {}


def test_settings_pick_up_values_from_a_dotenv_file(tmp_path, monkeypatch):
    path = tmp_path / ".env"
    path.write_text("ANTHROPIC_API_KEY=sk-ant-from-file\nMAX_AGENT_TURNS=3\n")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("MAX_AGENT_TURNS", raising=False)
    env.load(path)

    from app.config import Settings

    settings = Settings()
    assert settings.anthropic_api_key == "sk-ant-from-file"
    assert settings.max_agent_turns == 3
    assert settings.agent_is_live is True


# --- seller store -----------------------------------------------------------

RECORD = {
    "match": {"dispute_id": "PP-D-1", "invoice_number": "INV-1", "transaction_id": "TXN-1"},
    "order": {"order_id": "ORD-1"},
    "tracking": {"current_status": "DELIVERED"},
}


def test_records_are_found_by_every_identifier():
    store = SellerStore([RECORD])
    for identifier in ("PP-D-1", "INV-1", "TXN-1", "ORD-1"):
        assert store.lookup(identifier) is RECORD
    assert store.lookup("unknown") is None


def test_lookup_for_dispute_tries_each_identifier_the_dispute_carries():
    store = SellerStore([RECORD])
    dispute = {
        "dispute_id": "PP-D-UNSEEN",
        "disputed_transactions": [{"invoice_number": "INV-1", "seller_transaction_id": "other"}],
    }
    assert store.lookup_for_dispute(dispute) is RECORD


def test_lookup_for_dispute_returns_none_when_nothing_matches():
    store = SellerStore([RECORD])
    assert store.lookup_for_dispute({"dispute_id": "X", "disputed_transactions": [{}]}) is None


def test_a_flat_mapping_is_accepted_and_keyed_by_dispute_id():
    store = SellerStore.from_payload({"PP-D-9": {"order": {"order_id": "A"}}})
    assert store.lookup("PP-D-9")["order"]["order_id"] == "A"


def test_a_records_list_is_accepted():
    store = SellerStore.from_payload({"records": [RECORD]})
    assert len(store) == 1 and store.lookup("INV-1")


def test_a_bare_list_is_accepted():
    assert len(SellerStore.from_payload([RECORD])) == 1


def test_an_unusable_payload_is_rejected():
    with pytest.raises(ValueError, match="must be an object or a list"):
        SellerStore.from_payload("nonsense")


def test_a_missing_store_file_gives_an_empty_store(tmp_path):
    store = SellerStore.from_file(tmp_path / "absent.json")
    assert len(store) == 0 and store.lookup("anything") is None


def test_invalid_json_names_the_file(tmp_path):
    path = tmp_path / "store.json"
    path.write_text("{not json")
    with pytest.raises(ValueError, match="not valid JSON"):
        SellerStore.from_file(path)


def test_the_shipped_example_store_file_loads():
    store = SellerStore.from_file("data/store.example.json")
    assert len(store) == 2
    record = store.lookup("PP-D-10041")
    assert record["tracking"]["current_status"] == "DELIVERED"
    assert record["order"]["order_id"] == "HF-24181"
