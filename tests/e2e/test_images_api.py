"""Image attachments over HTTP: upload, attach, view, remove (no browser).

Against the shared live server, with fresh users per test so pending-image
counts and ownership are isolated. Files are inspected in the server's own
image directory (``LiveServer.images_dir``).
"""

from __future__ import annotations

import time
from collections.abc import Callable, Iterator
from datetime import date, datetime, timezone
from pathlib import Path

import httpx
import pytest

from harness import ApiClient, LiveServer, User
from imaging import CAMERA, decode, encode, halves, jpeg_with_gps, png_header_only

TODAY = datetime.now(timezone.utc).date()
PHOTO = encode(halves((600, 300)), "JPEG")
SIZES = {"thumb": 480, "display": 2048, "full": 4096}

Person = Callable[[], ApiClient]


@pytest.fixture
def person(live_server: LiveServer, make_user: Callable[..., User]) -> Iterator[Person]:
    clients: list[ApiClient] = []

    def make() -> ApiClient:
        client = ApiClient.logged_in(live_server.url, make_user("img"))
        clients.append(client)
        return client

    yield make
    for client in clients:
        client.close()


def files_of(server: LiveServer, image_id: str) -> list[Path]:
    return sorted(server.images_dir.rglob(f"{image_id}.*"))


def eventually(check: Callable[[], bool], timeout: float = 10.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if check():
            return True
        time.sleep(0.1)
    return check()


def day_html(client: ApiClient, day: date = TODAY) -> str:
    resp = client.http.get(f"/e/{day.isoformat()}", headers=client.headers)
    resp.raise_for_status()
    return resp.text


# -- upload, attach, view -------------------------------------------------


def test_upload_attach_and_view_every_variant(
    person: Person, live_server: LiveServer
) -> None:
    alice = person()
    resp = alice.upload_image(jpeg_with_gps(), filename="IMG_0412.jpg")
    assert resp.status_code == 201
    body = resp.json()
    image_id = body["id"]
    assert body["thumb"] == {"width": 200, "height": 300}  # rotated upright

    entry_id = alice.create_entry(TODAY, "With a photo", image_ids=[image_id])

    html = day_html(alice)
    assert f'id="entry-images-{entry_id}"' in html
    assert f'data-image-id="{image_id}"' in html
    assert f"/i/{image_id}/thumb" in html

    for variant, edge in SIZES.items():
        got = alice.get_image(image_id, variant)
        assert got.status_code == 200, variant
        assert got.headers["content-type"] == "image/webp"
        assert int(got.headers["content-length"]) == len(got.content)
        image = decode(got.content)
        assert image.format == "WEBP" and max(image.size) <= edge
        assert not image.getexif() and CAMERA.encode() not in got.content
        assert got.headers["x-content-type-options"] == "nosniff"
        assert got.headers["content-security-policy"] == "default-src 'none'; sandbox"
        assert got.headers["cache-control"] == "private, no-cache"
        assert got.headers["etag"] == f'"{image_id}.{variant}"'
        assert "Cookie" in got.headers["vary"]


def test_files_on_disk_reveal_nothing(person: Person, live_server: LiveServer) -> None:
    alice = person()
    source = jpeg_with_gps()
    image_id = alice.upload_image_id(source, filename="holiday-secret.jpg")

    files = files_of(live_server, image_id)
    assert [p.suffix for p in files] == [".display", ".full", ".thumb"]
    for path in files:
        blob = path.read_bytes()
        assert source[:64] not in blob
        for signature in (b"RIFF", b"WEBP", b"\xff\xd8\xff", b"Exif", b"holiday"):
            assert signature not in blob, (path.name, signature)
    assert not any("holiday" in str(p) for p in live_server.images_dir.rglob("*"))


def test_unchanged_image_is_not_sent_again(person: Person) -> None:
    alice = person()
    image_id = alice.upload_image_id(PHOTO)
    first = alice.get_image(image_id, "thumb")

    again = alice.get_image(
        image_id, "thumb", headers={"If-None-Match": first.headers["etag"]}
    )

    assert again.status_code == 304 and again.content == b""
    assert again.headers["etag"] == first.headers["etag"]


def test_entries_without_images_render_an_empty_slot(person: Person) -> None:
    alice = person()
    entry_id = alice.create_entry(TODAY, "Just words")
    html = day_html(alice)
    assert f'id="entry-images-{entry_id}"' in html
    assert 'entry-images"' not in html  # no <ul class="entry-images">


def test_images_show_in_the_tags_view(person: Person) -> None:
    alice = person()
    image_id = alice.upload_image_id(PHOTO)
    entry_id = alice.create_entry(TODAY, "Tagged with a photo", image_ids=[image_id])
    alice.add_tag(entry_id, "harbour")

    resp = alice.http.get("/t/harbour")
    resp.raise_for_status()
    assert f'data-image-id="{image_id}"' in resp.text


# -- who may see what -----------------------------------------------------


def test_other_users_cannot_see_use_or_delete_an_image(
    person: Person, live_server: LiveServer
) -> None:
    alice, bob = person(), person()
    image_id = alice.upload_image_id(PHOTO)

    assert bob.get_image(image_id, "thumb").status_code == 404
    etag = f'"{image_id}.thumb"'
    assert (
        bob.get_image(image_id, "thumb", headers={"If-None-Match": etag}).status_code
        == 404
    )
    assert bob.delete_image(image_id).status_code == 404
    assert bob.post_entry(TODAY, "Stealing", image_ids=[image_id]).status_code == 400
    assert "Stealing" not in day_html(bob)

    assert alice.get_image(image_id, "thumb").status_code == 200
    assert len(files_of(live_server, image_id)) == 3


def test_logged_out_requests_get_no_image(
    person: Person, live_server: LiveServer
) -> None:
    image_id = person().upload_image_id(PHOTO)
    with httpx.Client(base_url=live_server.url) as anonymous:
        resp = anonymous.get(f"/i/{image_id}/thumb")
        assert resp.status_code in (302, 401)
        assert b"RIFF" not in resp.content
        htmx = anonymous.get(f"/i/{image_id}/thumb", headers={"HX-Request": "true"})
        assert htmx.status_code == 401


def test_upload_requires_the_csrf_token(person: Person) -> None:
    resp = person().upload_image(PHOTO, csrf=False)
    assert resp.status_code == 400


@pytest.mark.parametrize("path", ["/i/NOPE/thumb", "/i/NOPE/original"])
def test_unknown_images_and_variants(person: Person, path: str) -> None:
    assert person().http.get(path).status_code == 404


# -- limits ---------------------------------------------------------------


@pytest.mark.parametrize(
    ("data", "status", "reason"),
    [
        (b"not an image at all", 415, "unsupported_format"),
        (b"<svg xmlns='http://www.w3.org/2000/svg'/>", 415, "unsupported_format"),
        (png_header_only(10_000, 6_000), 400, "too_many_pixels"),
        (PHOTO[: len(PHOTO) // 2], 400, "corrupt"),
    ],
    ids=["text", "svg", "pixel-bomb", "truncated"],
)
def test_bad_uploads_are_refused(
    person: Person, live_server: LiveServer, data: bytes, status: int, reason: str
) -> None:
    before = set(live_server.images_dir.rglob("*"))
    resp = person().upload_image(data)
    assert resp.status_code == status
    assert resp.json()["error"] == reason and resp.json()["message"]
    assert set(live_server.images_dir.rglob("*")) - before <= {
        p for p in live_server.images_dir.rglob("*") if p.is_dir()
    }


def test_oversized_upload_is_refused(person: Person) -> None:
    resp = person().upload_image(b"\xff" * (21 * 1024 * 1024))
    assert resp.status_code == 413


def test_missing_file_field(person: Person) -> None:
    alice = person()
    resp = alice.http.post("/i", data={"other": "x"}, headers=alice.headers)
    assert resp.status_code == 400


def test_at_most_eight_images_per_entry(person: Person) -> None:
    alice = person()
    ids = [alice.upload_image_id(PHOTO) for _ in range(9)]
    assert alice.post_entry(TODAY, "Nine", image_ids=ids).status_code == 400
    assert alice.post_entry(TODAY, "Eight", image_ids=ids[:8]).status_code == 200


def test_unsent_uploads_are_capped(person: Person) -> None:
    alice = person()
    small = encode(halves((20, 10)), "PNG")
    for _ in range(32):  # max_per_entry * 4
        alice.upload_image_id(small, filename="s.png", content_type="image/png")
    resp = alice.upload_image(small)
    assert resp.status_code == 429 and resp.json()["error"] == "too_many_pending"


def test_image_attached_elsewhere_cannot_be_reused(person: Person) -> None:
    alice = person()
    image_id = alice.upload_image_id(PHOTO)
    alice.create_entry(TODAY, "First", image_ids=[image_id])
    resp = alice.post_entry(TODAY, "Second", image_ids=[image_id])
    assert resp.status_code == 400
    assert "Second" not in day_html(alice)


# -- entries of images ----------------------------------------------------


def test_an_entry_can_be_only_images(person: Person) -> None:
    alice = person()
    image_id = alice.upload_image_id(PHOTO)
    entry_id = alice.create_entry(TODAY, "", image_ids=[image_id])
    assert f'data-image-id="{image_id}"' in day_html(alice)

    assert alice.post_entry(TODAY, "   ").status_code == 400  # still never nothing
    assert alice.update_entry(entry_id, "").status_code == 200  # images remain
    assert alice.update_entry(entry_id, "", image_ids=[]).status_code == 400


def test_editing_replaces_the_images_and_their_order(
    person: Person, live_server: LiveServer
) -> None:
    alice = person()
    a, b, c = (alice.upload_image_id(PHOTO) for _ in range(3))
    entry_id = alice.create_entry(TODAY, "Three photos", image_ids=[a, b])

    resp = alice.update_entry(entry_id, "Edited", image_ids=[c, a])

    assert resp.status_code == 200
    assert 'hx-swap-oob="outerHTML"' in resp.text
    assert resp.text.index(f'data-image-id="{c}"') < resp.text.index(
        f'data-image-id="{a}"'
    )
    assert f'data-image-id="{b}"' not in resp.text
    html = day_html(alice)
    assert html.index(f'data-image-id="{c}"') < html.index(f'data-image-id="{a}"')
    # The left-out image is an orphan now, and its files go soon.
    assert eventually(lambda: not files_of(live_server, b))
    assert alice.get_image(b, "thumb").status_code == 404


def test_editing_text_alone_keeps_the_images(person: Person) -> None:
    alice = person()
    image_id = alice.upload_image_id(PHOTO)
    entry_id = alice.create_entry(TODAY, "Before", image_ids=[image_id])

    resp = alice.update_entry(entry_id, "After")

    assert resp.status_code == 200 and "entry-images-" not in resp.text
    assert f'data-image-id="{image_id}"' in day_html(alice)


def test_clearing_all_images(person: Person) -> None:
    alice = person()
    image_id = alice.upload_image_id(PHOTO)
    entry_id = alice.create_entry(TODAY, "Keep the words", image_ids=[image_id])

    resp = alice.update_entry(entry_id, "Keep the words", image_ids=[])

    assert resp.status_code == 200
    assert f'data-image-id="{image_id}"' not in day_html(alice)


# -- removing -------------------------------------------------------------


def test_removing_an_unsent_image(person: Person, live_server: LiveServer) -> None:
    alice = person()
    image_id = alice.upload_image_id(PHOTO)
    resp = alice.delete_image(image_id)
    assert resp.status_code == 204
    assert not files_of(live_server, image_id)
    assert alice.get_image(image_id, "thumb").status_code == 404


def test_removing_an_image_from_an_entry(
    person: Person, live_server: LiveServer
) -> None:
    alice = person()
    keep, remove = alice.upload_image_id(PHOTO), alice.upload_image_id(PHOTO)
    entry_id = alice.create_entry(TODAY, "Two photos", image_ids=[keep, remove])

    resp = alice.delete_image(remove)

    assert resp.status_code == 200
    assert f'id="entry-images-{entry_id}"' in resp.text
    assert 'hx-swap-oob="outerHTML"' in resp.text
    assert f'data-image-id="{keep}"' in resp.text
    assert f'data-image-id="{remove}"' not in resp.text
    assert "HX-Trigger" in resp.headers
    assert not files_of(live_server, remove)
    assert len(files_of(live_server, keep)) == 3


def test_removing_the_only_image_of_an_entry_without_text(
    person: Person, live_server: LiveServer
) -> None:
    alice = person()
    image_id = alice.upload_image_id(PHOTO)
    entry_id = alice.create_entry(TODAY, "", image_ids=[image_id])

    refused = alice.delete_image(image_id)
    assert refused.status_code == 409 and refused.json()["error"] == "last_image"
    assert len(files_of(live_server, image_id)) == 3

    resp = alice.delete_image(image_id, with_entry=True)

    assert resp.status_code == 200
    assert f'id="entry-{entry_id}"' not in day_html(alice)
    assert eventually(lambda: not files_of(live_server, image_id))


def test_deleting_an_entry_removes_its_image_files(
    person: Person, live_server: LiveServer
) -> None:
    alice = person()
    image_id = alice.upload_image_id(PHOTO)
    entry_id = alice.create_entry(TODAY, "Soon gone", image_ids=[image_id])

    assert alice.delete_entry(entry_id).status_code == 200

    assert eventually(lambda: not files_of(live_server, image_id))
    assert alice.get_image(image_id, "thumb").status_code == 404


def test_deleting_the_account_removes_every_file(
    person: Person, live_server: LiveServer
) -> None:
    alice, bob = person(), person()
    sent = alice.upload_image_id(PHOTO)
    alice.create_entry(TODAY, "Mine", image_ids=[sent])
    unsent = alice.upload_image_id(PHOTO)
    theirs = bob.upload_image_id(PHOTO)

    resp = alice.http.delete("/profile", headers=alice.headers)

    assert resp.status_code == 204
    assert not files_of(live_server, sent) and not files_of(live_server, unsent)
    assert len(files_of(live_server, theirs)) == 3


# -- damage ---------------------------------------------------------------


def test_a_tampered_file_is_refused_and_logged(
    person: Person, live_server: LiveServer
) -> None:
    alice = person()
    image_id = alice.upload_image_id(PHOTO)
    path = next(p for p in files_of(live_server, image_id) if p.suffix == ".display")
    blob = bytearray(path.read_bytes())
    blob[40] ^= 1
    path.write_bytes(bytes(blob))

    resp = alice.get_image(image_id, "display")

    assert resp.status_code == 404
    assert b"RIFF" not in resp.content
    assert eventually(
        lambda: (
            f"Image {image_id} (display) failed to decrypt" in live_server.log_tail(400)
        )
    )
    assert alice.get_image(image_id, "thumb").status_code == 200  # others unharmed


def test_heic_photos_are_accepted(person: Person) -> None:
    from imaging import heic

    alice = person()
    resp = alice.upload_image(
        heic(halves((600, 400))), filename="IMG_0001.HEIC", content_type="image/heic"
    )
    assert resp.status_code == 201
    image = decode(alice.get_image(resp.json()["id"], "full").content)
    assert image.format == "WEBP" and image.size == (600, 400)
