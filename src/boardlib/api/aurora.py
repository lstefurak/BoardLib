from __future__ import annotations

import datetime
import os
import uuid

import bs4
import requests
import pandas as pd

import boardlib.db.aurora


BASE_SYNC_DATE = "1970-01-01 00:00:00.000000"
DEFAULT_MAX_SYNC_PAGES = 100
HOST_BASES = {
    "aurora": "auroraboardapp",
    "decoy": "decoyboardapp",
    "grasshopper": "grasshopperboardapp",
    "kilter": "kilterboardapp",
    "soill": "soillboardapp",
    "tension": "tensionboardapp2",
    "touchstone": "touchstoneboardapp",
}
WEB_HOSTS = {
    board: f"https://{host_base}.com" for board, host_base in HOST_BASES.items()
}


def login(board, username, password):
    headers = {
        "Accept": "application/json",
        "Content-Type": "application/json",
        "Connection": "keep-alive",
        "Accept-Language": "en-AU,en;q=0.9",
        "Accept-Encoding": "gzip, deflate, br",
        "User-Agent": "Kilter%20Board/202 CFNetwork/1568.100.1 Darwin/24.0.0",
    }

    response = requests.post(
        f"{WEB_HOSTS[board]}/sessions",
        json={
            "username": username,
            "password": password,
            "tou": "accepted",
            "pp": "accepted",
            "ua": "app",
        },
        headers=headers,
    )
    if response.status_code == requests.codes["unprocessable_entity"]:
        raise ValueError(
            "Invalid username or password. Please check your credentials and try again."
        )
    response.raise_for_status()
    return response.json()["session"]


def explore(board, token):
    response = requests.get(
        f"{WEB_HOSTS[board]}/explore",
        headers={"cookie": f"token={token}"},
    )
    response.raise_for_status()
    return response.json()


def get_ascents(board, token, session=None):
    return [
        ascent
        for sync_data in sync(board, {"ascents": BASE_SYNC_DATE}, token, session=session)
        for ascent in sync_data.get("ascents", [])
    ]


def get_attempts(board, token, session=None):
    return [
        bid
        for sync_data in sync(board, {"bids": BASE_SYNC_DATE}, token, session=session)
        for bid in sync_data.get("bids", [])
    ]


def get_gyms(board):
    """
    :return:
        {
            "gyms": [
                {
                    'id': 373656,
                    'username': '<username>',
                    'name': '<name>',
                    'latitude': 48.10135,
                    'longitude': 11.30113
                },
                ...
            ]
        }
    """
    response = requests.get(f"{WEB_HOSTS[board]}/pins?gyms=1")
    response.raise_for_status()
    return response.json()


def get_user(board, token, user_id):
    response = requests.get(
        f"{WEB_HOSTS[board]}/users/{user_id}",
        headers={"cookie": f"token={token}"},
    )
    response.raise_for_status()
    return response.json()


def get_climb_stats(board, token, climb_uuid, angle):
    response = requests.get(
        f"{WEB_HOSTS[board]}/climbs/{climb_uuid}/stats",
        params={"angle": angle},
        headers={"cookie": f"token={token}"},
    )
    response.raise_for_status()
    return response.json()


def get_climb_name(board, climb_uuid):
    response = requests.get(f"{WEB_HOSTS[board]}/climbs/{climb_uuid}")
    response.raise_for_status()
    soup = bs4.BeautifulSoup(response.text, "html.parser")
    heading = soup.find("h1")
    return heading.get_text(strip=True) if heading else None


def _sync_payload(tables_and_sync_dates):
    # Build URL-encoded form data manually - Aurora expects this format!
    return "&".join(
        f"{requests.utils.quote(table)}={requests.utils.quote(sync_date)}"
        for table, sync_date in tables_and_sync_dates.items()
    )


def user_sync(board, table_name, token):
    response = requests.post(
        f"{WEB_HOSTS[board]}/sync",
        data=_sync_payload({table_name: BASE_SYNC_DATE}),
        headers={
            "Cookie": f"token={token}",
            "Content-Type": "application/x-www-form-urlencoded",
        },
    )
    response.raise_for_status()
    return response.json()


def sync(board, tables_and_sync_dates, token=None, max_pages=DEFAULT_MAX_SYNC_PAGES, session=None):
    session = session or requests
    headers = {
        "Accept": "application/json",
        "User-Agent": "Kilter%20Board/202 CFNetwork/1568.100.1 Darwin/24.0.0",
        "Content-Type": "application/x-www-form-urlencoded",
    }
    if token:
        headers["Cookie"] = f"token={token}"

    payload_dict = dict(tables_and_sync_dates)
    page_count = 0
    complete = False

    while not complete and page_count < max_pages:
        response = session.post(
            f"{WEB_HOSTS[board]}/sync",
            data=_sync_payload(payload_dict),
            headers=headers,
        )
        response.raise_for_status()
        response_json = response.json()
        complete = response_json.pop("_complete", False)
        yield response_json

        # Update payload with the last sync date for each table.
        sync_kinds = ("user_syncs", "shared_syncs") if token else ("shared_syncs",)
        for sync_kind in sync_kinds:
            for table_sync in response_json.get(sync_kind, []):
                table_name = table_sync.get("table_name")
                last_synchronized_at = table_sync.get("last_synchronized_at")
                if table_name in payload_dict and last_synchronized_at:
                    payload_dict[table_name] = last_synchronized_at

        page_count += 1


def sync_local_database(board, db_path, token, max_pages=DEFAULT_MAX_SYNC_PAGES, progress=None):
    """Pull shared-table updates from the board API into a local database copy.

    :param progress: Optional callback called as
        progress(table_name, page_row_count, cumulative_row_count) after each
        synced page.
    :return: A dictionary mapping table names to total rows synced.
    """
    tables_and_sync_dates = boardlib.db.aurora.get_shared_syncs(db_path)
    row_counts_totals = {}
    with requests.Session() as session:
        for sync_result in sync(
            board,
            tables_and_sync_dates,
            token=token,
            max_pages=max_pages,
            session=session,
        ):
            row_counts = boardlib.db.aurora.sync_shared_tables(db_path, sync_result)
            for table_name, row_count in row_counts.items():
                row_counts_totals[table_name] = (
                    row_counts_totals.get(table_name, 0) + row_count
                )
                if progress:
                    progress(table_name, row_count, row_counts_totals[table_name])
    return row_counts_totals


def gym_boards(board):
    for gym in get_gyms(board)["gyms"]:
        yield {
            "name": gym["name"],
            "latitude": gym["latitude"],
            "longitude": gym["longitude"],
        }


def download_images(board, database_path, output_directory, composite=False):
    """
    Download all images for a given board to the specified directory.
    
    :param board: The board name
    :param database_path: Path to the SQLite database file
    :param output_directory: Directory to save the downloaded images
    :param composite: If true, build composite layout images for each board layout
    """
    os.makedirs(output_directory, exist_ok=True)
    image_filenames = boardlib.db.aurora.get_image_filenames(database_path)
    api_host = f"https://api.{HOST_BASES[board]}.com"

    with requests.Session() as session:
        for image_filename in image_filenames:
            # Create subdirectories if needed (e.g., for product_sizes_layouts_sets/1-v4.png)
            output_path = os.path.join(output_directory, image_filename)
            os.makedirs(os.path.dirname(output_path), exist_ok=True)

            # Skip download if file already exists
            if os.path.exists(output_path):
                print(f"Skipping {image_filename} (already exists)")
                continue

            response = session.get(f"{api_host}/img/{image_filename}")
            response.raise_for_status()

            with open(output_path, "wb") as output_file:
                output_file.write(response.content)

    if (composite):
        # Imported lazily so callers that only need logbook/database features
        # (e.g. the Lambda backend, or non-composite downloads) do not require
        # Pillow to be installed.
        import boardlib.util.images

        # Get the layouts-image-path dict from the database
        layouts_images_dict = boardlib.db.aurora.get_layouts_images_dict(database_path)
        
        # Construct the images one at a time.
        for (layout, product_size), image_names in layouts_images_dict.items():
            image_path = os.path.join(output_directory, layout, f"{product_size}.png")
            boardlib.util.images.overlay_images(output_directory, image_names, image_path)

def generate_uuid():
    return str(uuid.uuid4()).replace("-", "")


def save_ascent(
    board,
    token,
    user_id,
    climb_uuid,
    angle,
    is_mirror,
    attempt_id,
    bid_count,
    quality,
    difficulty,
    is_benchmark,
    comment,
    climbed_at,
):
    uuid = generate_uuid()
    response = requests.put(
        f"{WEB_HOSTS[board]}/ascents/save/{uuid}",
        headers={"Cookie": f"token={token}"},
        json={
            "user_id": user_id,
            "uuid": uuid,
            "climb_uuid": climb_uuid,
            "angle": angle,
            "is_mirror": is_mirror,
            "attempt_id": attempt_id,
            "bid_count": bid_count,
            "quality": quality,
            "difficulty": difficulty,
            "is_benchmark": is_benchmark,
            "comment": comment,
            "climbed_at": climbed_at,
        },
    )
    response.raise_for_status()
    return response.json()


def save_attempt(
    board,
    token,
    user_id,
    climb_uuid,
    angle,
    is_mirror,
    bid_count,
    comment,
    climbed_at,
):
    uuid = generate_uuid()
    response = requests.put(
        f"{WEB_HOSTS[board]}/bids/save",
        headers={"Cookie": f"token={token}"},
        json={
            "user_id": user_id,
            "uuid": uuid,
            "climb_uuid": climb_uuid,
            "angle": angle,
            "is_mirror": is_mirror,
            "bid_count": bid_count,
            "comment": comment,
            "climbed_at": climbed_at,
        },
    )
    response.raise_for_status()
    return response.json()


def save_climb(
    board,
    token,
    layout_id,
    setter_id,
    name,
    description,
    is_draft,
    frames,
    frames_count=1,
    frames_pace=0,
    angle=None,
):
    uuid = generate_uuid()
    data = {
        "uuid": uuid,
        "layout_id": layout_id,
        "setter_id": setter_id,
        "name": name,
        "description": description,
        "is_draft": is_draft,
        "frames_count": frames_count,
        "frames_pace": frames_pace,
        "frames": frames,
    }
    if angle is not None:
        data["angle"] = angle

    response = requests.put(
        f"{WEB_HOSTS[board]}/climbs/save",
        headers={"Cookie": f"token={token}"},
        json=data,
    )
    response.raise_for_status()
    return response.json()


def bids_logbook_entries(board, token, db_path, session=None):
    raw_entries = get_attempts(board, token, session=session)
    climb_names = boardlib.db.aurora.get_climb_name_mapping(
        db_path, [raw_entry["climb_uuid"] for raw_entry in raw_entries]
    )

    for raw_entry in raw_entries:
        climb_name = climb_names.get(raw_entry["climb_uuid"])

        yield {
            "climb_uuid": raw_entry["climb_uuid"],
            "user_id": raw_entry["user_id"],
            "climb_name": climb_name,
            "angle": raw_entry["angle"],
            "is_mirror": raw_entry["is_mirror"],
            "bid_count": raw_entry["bid_count"],
            "comment": raw_entry["comment"],
            "climbed_at": raw_entry["climbed_at"],
            "created_at": raw_entry["created_at"],
        }


def process_raw_ascent_entries(raw_ascents_entries, board, db_path):
    ascents_entries = []
    difficulty_mapping = boardlib.db.aurora.get_difficulty_mapping(db_path)
    listed_entries = [entry for entry in raw_ascents_entries if entry["is_listed"]]
    climb_names = boardlib.db.aurora.get_climb_name_mapping(
        db_path, [entry["climb_uuid"] for entry in listed_entries]
    )
    climb_stats = boardlib.db.aurora.get_climb_stats_mapping(
        db_path, [entry["climb_uuid"] for entry in listed_entries]
    )
    for raw_entry in listed_entries:
        climb_name = climb_names.get(raw_entry["climb_uuid"])
        stats = climb_stats.get((raw_entry["climb_uuid"], raw_entry["angle"]), {})

        ascents_entries.append(
            {
                "board": board,
                "angle": raw_entry["angle"],
                "climb_uuid": raw_entry["climb_uuid"],
                "name": climb_name,
                "date": datetime.datetime.strptime(
                    raw_entry["climbed_at"], "%Y-%m-%d %H:%M:%S"
                ),
                "logged_grade": difficulty_to_grade(
                    difficulty_mapping, raw_entry["difficulty"]
                ),
                "displayed_grade": difficulty_to_grade(
                    difficulty_mapping, stats.get("display_difficulty")
                ),
                "is_benchmark": bool(stats.get("benchmark_difficulty")),
                "tries": (
                    raw_entry["attempt_id"]
                    if raw_entry["attempt_id"]
                    else raw_entry["bid_count"]
                ),
                "is_mirror": raw_entry["is_mirror"],
                "comment": raw_entry["comment"],
                "ascensionist_count": stats.get("ascensionist_count"),
                "quality_average": stats.get("quality_average"),
            }
        )
    return ascents_entries


def summarize_bids(bids_df, board):
    bids_summary = (
        bids_df.groupby(
            [
                "climb_uuid",
                "climb_name",
                bids_df["climbed_at"].dt.date,
                "is_mirror",
                "angle",
            ]
        )
        .agg({"bid_count": "sum"})
        .reset_index()
        .rename(columns={"climbed_at": "date"})
    )
    bids_summary["is_ascent"] = False
    bids_summary["tries"] = bids_summary["bid_count"]
    bids_summary["board"] = board  # Ensure the 'board' column is included
    return bids_summary


def combine_ascents_and_bids(ascents_df, bids_summary, db_path):
    final_logbook = []
    difficulty_mapping = boardlib.db.aurora.get_difficulty_mapping(db_path)

    # summarize_bids groups by exactly this key, so each key maps to one row.
    # Bids matched to an ascent are removed; the leftovers become attempt rows.
    leftover_bids = {
        (bid_row["climb_uuid"], bid_row["date"], bid_row["is_mirror"], bid_row["angle"]): bid_row
        for _, bid_row in bids_summary.iterrows()
    }

    for _, ascent_row in ascents_df.iterrows():
        key = (
            ascent_row["climb_uuid"],
            ascent_row["date"].date(),
            ascent_row["is_mirror"],
            ascent_row["angle"],
        )
        bid_row = leftover_bids.pop(key, None)
        tries = ascent_row["tries"] + (bid_row["tries"] if bid_row is not None else 0)

        final_logbook.append(
            {
                # Used for Climbdex to uniquely identify climbs at a particular angle
                "climb_angle_uuid": f"{ascent_row['climb_uuid']}-{ascent_row['angle']}",
                "climb_uuid": ascent_row["climb_uuid"],
                "board": ascent_row["board"],
                "angle": ascent_row["angle"],
                "climb_name": ascent_row["name"],
                "date": ascent_row["date"],
                "logged_grade": ascent_row["logged_grade"],
                "displayed_grade": ascent_row.get("displayed_grade", None),
                "is_benchmark": ascent_row.get("is_benchmark", None),
                "tries": tries,
                "is_mirror": ascent_row["is_mirror"],
                "is_ascent": True,
                "comment": ascent_row["comment"],
                "ascensionist_count": ascent_row.get("ascensionist_count", None),
                "quality_average": ascent_row.get("quality_average", None),
            }
        )

    climb_stats = boardlib.db.aurora.get_climb_stats_mapping(
        db_path, [bid_row["climb_uuid"] for bid_row in leftover_bids.values()]
    )
    for bid_row in leftover_bids.values():
        stats = climb_stats.get((bid_row["climb_uuid"], bid_row["angle"]), {})

        final_logbook.append(
            {
                "climb_angle_uuid": f"{bid_row['climb_uuid']}-{bid_row['angle']}",
                "climb_uuid": bid_row["climb_uuid"],
                "board": bid_row["board"],
                "angle": bid_row["angle"],
                "climb_name": bid_row["climb_name"],
                "date": bid_row["date"],
                "logged_grade": None,
                "displayed_grade": difficulty_to_grade(
                    difficulty_mapping, stats.get("display_difficulty")
                ),
                "is_benchmark": bool(stats.get("benchmark_difficulty")),
                "tries": bid_row["tries"],
                "is_mirror": bid_row["is_mirror"],
                "is_ascent": False,
                "comment": bid_row.get(
                    "comment", None
                ),  # Use .get() to safely handle missing 'comment'
                "ascensionist_count": stats.get("ascensionist_count"),
                "quality_average": stats.get("quality_average"),
            }
        )
    return final_logbook


def logbook_entries(board, token, db_path):
    with requests.Session() as session:
        bids_entries = list(bids_logbook_entries(board, token, db_path, session=session))
        raw_ascents_entries = get_ascents(board, token, session=session)

    if not bids_entries and not raw_ascents_entries:
        return pd.DataFrame(
            columns=[
                "climb_angle_uuid",
                "climb_uuid",
                "board",
                "angle",
                "climb_name",
                "date",
                "logged_grade",
                "displayed_grade",
                "is_benchmark",
                "tries",
                "is_mirror",
                "is_ascent",
                "comment",
                "ascensionist_count",
                "quality_average",
            ]
        )

    if bids_entries:
        bids_df = pd.DataFrame(bids_entries)
        bids_df["climbed_at"] = pd.to_datetime(bids_df["climbed_at"])
        bids_summary = summarize_bids(bids_df, board)
    else:
        bids_summary = pd.DataFrame(
            columns=[
                "climb_uuid",
                "climb_name",
                "date",
                "is_mirror",
                "angle",
                "tries",
                "board",
            ]
        )

    if raw_ascents_entries:
        ascents_entries = process_raw_ascent_entries(
            raw_ascents_entries, board, db_path
        )
        ascents_df = pd.DataFrame(ascents_entries)
    else:
        ascents_df = pd.DataFrame(
            columns=[
                "board",
                "angle",
                "climb_uuid",
                "name",
                "date",
                "logged_grade",
                "displayed_grade",
                "is_benchmark",
                "tries",
                "is_mirror",
                "comment",
                "ascensionist_count",
                "quality_average",
            ]
        )

    final_logbook = combine_ascents_and_bids(ascents_df, bids_summary, db_path)

    full_logbook_df = pd.DataFrame(
        final_logbook,
        columns=[
            "climb_angle_uuid",
            "climb_uuid",
            "board",
            "angle",
            "climb_name",
            "date",
            "logged_grade",
            "displayed_grade",
            "is_benchmark",
            "tries",
            "is_mirror",
            "is_ascent",
            "comment",
            "ascensionist_count",
            "quality_average",
        ],
    )
    full_logbook_df["date"] = pd.to_datetime(full_logbook_df["date"])

    if full_logbook_df.empty:
        full_logbook_df["sessions_count"] = pd.Series(dtype="int")
        full_logbook_df["tries_total"] = pd.Series(dtype="int")
        full_logbook_df["is_repeat"] = pd.Series(dtype="bool")
        return full_logbook_df

    group_columns = ["climb_name", "is_mirror", "angle"]
    full_logbook_df = full_logbook_df.sort_values(group_columns + ["date"])
    full_logbook_df["_session_date"] = full_logbook_df["date"].dt.date
    full_logbook_df["sessions_count"] = full_logbook_df.groupby(
        group_columns, dropna=False
    )["_session_date"].transform(
        lambda session_dates: pd.factorize(session_dates)[0] + 1
    )
    full_logbook_df["tries_total"] = full_logbook_df.groupby(
        group_columns, dropna=False
    )["tries"].cumsum()
    full_logbook_df = full_logbook_df.drop(columns=["_session_date"])

    full_logbook_df["is_repeat"] = full_logbook_df.duplicated(
        subset=group_columns, keep="first"
    )
    full_logbook_df = full_logbook_df.sort_values(by="date")

    return full_logbook_df


def user_followers(board: str, token: str, user_id: int):
    """
    Get all accounts that follow the given user
    :param board:
    :param token:
    :param user_id:
    :return:
        {
            'users': [
                {
                    'id': int,
                    'username': str,
                    'name': optional str,
                    'avatar': optional str,
                },
                ...
            ]
        }
    """
    response = requests.get(
        f"{WEB_HOSTS[board]}/users/{user_id}/followers",
        headers={"cookie": f"token={token}"},
    )
    response.raise_for_status()
    return response.json()


def user_followees(board: str, token: str, user_id: int):
    """
    Get all accounts the given user follows
    :param board:
    :param token:
    :param user_id:
    :return:
        {
            'users': [
                {
                    'id': int,
                    'username': str,
                    'name': optional str,
                    'avatar': optional str,
                },
                ...
            ]
        }
    """
    response = requests.get(
        f"{WEB_HOSTS[board]}/users/{user_id}/followees",
        headers={"cookie": f"token={token}"},
    )
    response.raise_for_status()
    return response.json()


def follow(board: str, token: str, your_user_id: int, id_to_follow: int):
    """
    Follow a user
    """
    response = requests.post(
        f"{WEB_HOSTS[board]}/follows/save",
        headers={"cookie": f"token={token}"},
        data={
            "followee_id": id_to_follow,
            "follower_id": your_user_id,
            "state": "pending",
        },
    )
    response.raise_for_status()
    return response.json()


def unfollow(board: str, token: str, your_user_id: int, id_to_follow: int):
    """
    Unfollow a user
    """
    response = requests.post(
        f"{WEB_HOSTS[board]}/follows/save",
        headers={"cookie": f"token={token}"},
        data={
            "followee_id": id_to_follow,
            "follower_id": your_user_id,
            "state": "unfollowed",
        },
    )
    response.raise_for_status()
    return response.json()


def get_notifications(board: str, token: str, included_types: list[str] = None):
    """
    Get all notifications for the given user
    :param board:
    :param token:
    :param included_types: a list of notification types to include in the response. Optional values:
    :return:
        {
            'notifications': [
                {
                    '_type': str,
                    ...
                },
                ...
            ]
        }
    """

    if included_types is None:
        included_types = ["climbs", "follows", "users", "ascents", "likes"]

    response = requests.get(
        f"{WEB_HOSTS[board]}/notifications",
        params={t: 1 for t in included_types},
        headers={"cookie": f"token={token}"},
    )
    response.raise_for_status()
    return response.json()


def difficulty_to_grade(difficulty_mapping, difficulty):
    return (
        difficulty_mapping.get(int(round(difficulty)), None)
        if difficulty is not None
        else None
    )
