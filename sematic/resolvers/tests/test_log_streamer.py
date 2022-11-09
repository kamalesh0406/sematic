# Standard Library
import pathlib
import sys
import tempfile
import time

# Sematic
from sematic.resolvers.log_streamer import _tail_log_file, ingested_logs
from sematic.utils.retry import retry


@retry(AssertionError, tries=3)  # this test is somewhat dependent on relative timings.
def test_ingested_logs():
    with tempfile.NamedTemporaryFile(delete=False) as log_file:
        remote_prefix = "foo/bar"
        upload_interval = 0.1
        with ingested_logs(
            log_file.name,
            upload_interval_seconds=upload_interval,
            remote_prefix=remote_prefix,
            uploader=mock_uploader,
        ):
            print("From stdout")
            print("From stderr", file=sys.stderr)
            time.sleep(
                50 * upload_interval
            )  # ensure at least one upload happens before final flush
            print("From stdout late")
            print("From stderr late", file=sys.stderr)

    upload_number = 0
    uploads = []
    while pathlib.Path(_upload_path(log_file.name, upload_number)).exists():
        with open(_upload_path(log_file.name, upload_number), "r") as upload:
            uploads.append(list(upload))
        upload_number += 1

    assert len(uploads) > 1  # should be at least one timed, and the final flush

    everything = [
        "From stdout\n",
        "From stderr\n",
        "From stdout late\n",
        "From stderr late\n",
    ]

    # early uploads should have only early stuff, late uploads should
    # have late stuff. So no single upload should have everything.
    assert not any(upload == [f"{remote_prefix}\n"] + everything for upload in uploads)

    all_upload_contents = concat_uploads(uploads)

    # everything uploaded should contain all log lines, with
    # no duplicated lines.
    assert all_upload_contents == everything


def test_ingested_logs_uncaught():
    raised = False
    started = time.time()
    message = "Uncaught error!"
    try:
        with tempfile.NamedTemporaryFile(delete=False) as log_file:
            remote_prefix = "foo/bar"
            upload_interval = 100  # should exit early despite the long interval
            with ingested_logs(
                log_file.name,
                upload_interval_seconds=upload_interval,
                remote_prefix=remote_prefix,
                uploader=mock_uploader,
            ):
                raise ValueError(message)
    except ValueError:
        raised = True
    assert raised
    assert time.time() - started < upload_interval

    upload_number = 0
    uploads = []
    while pathlib.Path(_upload_path(log_file.name, upload_number)).exists():
        with open(_upload_path(log_file.name, upload_number), "r") as upload:
            uploads.append(list(upload))
        upload_number += 1

    assert len(uploads) >= 1
    assert message in "\n".join(concat_uploads(uploads))


def test_tail_log_file():
    printed = []

    def print_func(to_print, end="\n", **kwargs):
        printed.append(to_print + end)

    with tempfile.NamedTemporaryFile(delete=False) as log_file:
        remote_prefix = "foo/bar"
        upload_interval = 0.1
        line_template = "This is line {}"
        stderr_line_template = "This is stderr line {}"
        n_lines = 100
        with ingested_logs(
            log_file.name,
            upload_interval_seconds=upload_interval,
            remote_prefix=remote_prefix,
            uploader=mock_uploader,
        ):
            for i in range(n_lines):
                print(line_template.format(i))
                print(stderr_line_template.format(i), file=sys.stderr)
        _tail_log_file(log_file.name, max_tail_bytes=0, print_func=print_func)

        assert len(printed) == 0
        _tail_log_file(
            log_file.name, max_tail_bytes=6 * len(line_template), print_func=print_func
        )
        assert len(printed) > 0
        assert printed[-1] == stderr_line_template.format(n_lines - 1) + "\n"
        assert printed[-2] == line_template.format(n_lines - 1) + "\n"
        has_ellipsis = any(
            line.replace("\n", "").replace("\t", "") == "..." for line in printed
        )
        assert has_ellipsis

        printed.clear()

        # all lines present
        _tail_log_file(log_file.name, max_tail_bytes=2**30, print_func=print_func)
        assert printed[-2 * n_lines] == line_template.format(0) + "\n"
        assert printed[-2 * n_lines + 1] == stderr_line_template.format(0) + "\n"
        assert printed[-2] == line_template.format(n_lines - 1) + "\n"
        assert printed[-1] == stderr_line_template.format(n_lines - 1) + "\n"
        has_ellipsis = any(
            line.replace("\n", "").replace("\t", "") == "..." for line in printed
        )
        assert not has_ellipsis


def _upload_path(file_uploaded, upload_number):
    file_prefix = f"{file_uploaded}.uploaded_to"
    upload_path = f"{file_prefix}.{upload_number}"
    return upload_path


# Why use this rather than patched storage? Because the uploads happen from
# a subprocess, and patches/fixtures don't work in subprocesses
def mock_uploader(file_path, remote_prefix):
    remote_prefix = remote_prefix.encode("utf8")
    upload_number = 0
    while pathlib.Path(_upload_path(file_path, upload_number)).exists():
        upload_number += 1
    with open(_upload_path(file_path, upload_number), "wb") as record:
        record.write(remote_prefix + b"\n")
        with open(file_path, "rb") as log:
            for line in log:
                record.write(line)


def concat_uploads(uploads):
    all_upload_contents = []
    for upload in uploads:
        all_upload_contents.extend(
            upload[1:]
        )  # 1st line of each upload is remote prefix
    return all_upload_contents
