import collections
import contextlib
import io
import sqlite3
import zipfile

import requests


# sqlite3's connection context manager only manages transactions; it does not
# close the connection. This wrapper does both, so callers never leak handles
# (which also keep the database file locked on Windows).
@contextlib.contextmanager
def _connection(database):
    connection = sqlite3.connect(database)
    try:
        with connection:
            yield connection
    finally:
        connection.close()


def _chunks(items, size=500):
    for start in range(0, len(items), size):
        yield items[start : start + size]


APP_PACKAGE_NAMES = {
    "aurora": "auroraboard",
    "decoy": "decoyboard",
    "grasshopper": "grasshopperboard",
    "kilter": "kilterboard",
    "soill": "soillboard",
    "tension": "tensionboard2",
    "touchstone": "touchstoneboard",
}


def download_database(board, output_file):
    """
    The sqlite3 database is stored in the assets folder of the APK files for the Android app of each board.

    This function downloads the latest APK file for the board's Android app and extracts the database from it.
    :param board: The board to download the database for.
    :param output_file: The file to write the database to.
    """
    app_package_name = APP_PACKAGE_NAMES[board]
    response = requests.get(
        f"https://d.apkpure.net/b/APK/com.auroraclimbing.{app_package_name}",
        params={"version": "latest"},
        # Some user-agent is required, 403 if not included
        headers={
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36"
        },
    )
    response.raise_for_status()

    bundle_file = io.BytesIO(response.content)
    with zipfile.ZipFile(bundle_file, "r") as zip_file:
        try: 
            apk_file = io.BytesIO(zip_file.read(f"com.auroraclimbing.{app_package_name}.apk"))
        except KeyError:
            # Fallback to old APK directory structure to support older versions
            with open(output_file, "wb") as output_file:
                output_file.write(zip_file.read("assets/db.sqlite3"))
        else:
            with zipfile.ZipFile(apk_file, "r") as main_zip:
                with open(output_file, "wb") as output_file:
                    output_file.write(main_zip.read("assets/db.sqlite3"))


def get_shared_syncs(database):
    """
    Retrieve the mapping of tables names to last sync dates for the shared (public) tables in the database.

    :param database: The path to the SQLite database file.
    :return: A dictionary mapping table names to their last synchronized date.
    """
    with _connection(database) as connection:
        result = connection.execute(
            "SELECT table_name, last_synchronized_at FROM shared_syncs"
        )
        return {
            table_name: last_synchronized_at
            for table_name, last_synchronized_at in result.fetchall()
        }


def sync_shared_tables(database, sync_result):
    """
    Sync the shared tables in the database with the provided sync results from a sync API request.

    :param database: The path to the SQLite database file.
    :param row_counts: A dictionary mapping table names to number of rows inserted/updated/deleted.
    """
    with _connection(database) as connection:
        row_counts = {}
        for table_name, rows in sync_result.items():
            ROW_INSERTERS.get(table_name, insert_rows_default)(
                connection, table_name, rows
            )
            row_counts[table_name] = len(rows)

        return row_counts


def insert_rows_default(connection, table_name, rows):
    """
    Insert or replace the given rows into the specified table
    :param connection: The SQLite connection object.
    :param table_name: The name of the table to insert rows into.
    :param rows: The list of rows to insert.
    """
    pragma_result = connection.execute(f"PRAGMA table_info('{table_name}')")
    value_params = ", ".join(f":{row[1]}" for row in pragma_result.fetchall())
    connection.executemany(
        f"INSERT OR REPLACE INTO {table_name} VALUES ({value_params})",
        (collections.defaultdict(lambda: None, row) for row in rows),
    )


def insert_rows_climb_stats(connection, table_name, rows):
    """
    Insert/replace/delete the given rows into the climb_stats table. When a row has no display_difficulty, this means the row should be deleted.
    :param connection: The SQLite connection object.
    :param table_name: The name of the table to insert rows into. Should be "climb_stats".
    :param rows: The list of rows to insert.
    """
    pragma_result = connection.execute(f"PRAGMA table_info('{table_name}')")
    value_params = ", ".join(f":{row[1]}" for row in pragma_result.fetchall())
    insert_rows = []
    delete_rows = []
    for row in rows:
        row_dict = collections.defaultdict(
            lambda: None,
            row,
            display_difficulty=(
                row["benchmark_difficulty"]
                if row.get("benchmark_difficulty") is not None
                else row.get("difficulty_average")
            ),
        )
        # Only a missing difficulty marks a deletion; 0 is a valid difficulty.
        row_list = (
            insert_rows if row_dict["display_difficulty"] is not None else delete_rows
        )
        row_list.append(row_dict)

    connection.executemany(
        f"INSERT OR REPLACE INTO {table_name} VALUES ({value_params})",
        insert_rows,
    )
    for row in delete_rows:
        connection.execute(
            f"DELETE FROM {table_name} WHERE climb_uuid = :climb_uuid AND angle = :angle",
            row,
        )


ROW_INSERTERS = {
    "climb_stats": insert_rows_climb_stats,
}


def get_difficulty(database, climb_uuid, angle):
    with _connection(database) as connection:
        results = connection.execute(
            "SELECT display_difficulty, benchmark_difficulty FROM climb_stats WHERE climb_uuid = ? AND angle = ?",
            (climb_uuid, angle),
        )
        return next(results, [None, None])


def get_difficulty_mapping(database):
    with _connection(database) as connection:
        return {
            row[0]: row[1]
            for row in connection.execute(
                "SELECT difficulty, boulder_name FROM difficulty_grades"
            )
        }


def get_climb_name(database, climb_uuid):
    with _connection(database) as connection:
        results = connection.execute(
            "SELECT name FROM climbs WHERE uuid = ?", (climb_uuid,)
        )
        return next(results, [None])[0]


def get_climb_name_mapping(database, climb_uuids):
    """
    Batch variant of get_climb_name: one connection and a few chunked queries
    instead of a connection per climb.

    :return: A dictionary mapping climb uuid to climb name.
    """
    unique_uuids = list(set(climb_uuids))
    mapping = {}
    with _connection(database) as connection:
        for chunk in _chunks(unique_uuids):
            placeholders = ", ".join("?" * len(chunk))
            for climb_uuid, name in connection.execute(
                f"SELECT uuid, name FROM climbs WHERE uuid IN ({placeholders})", chunk
            ):
                mapping[climb_uuid] = name
    return mapping


def get_climb_stats_mapping(database, climb_uuids):
    """
    Batch lookup of community stats from the climb_stats table.

    :return: A dictionary mapping (climb_uuid, angle) to a dict with
        display_difficulty, benchmark_difficulty, ascensionist_count, and
        quality_average.
    """
    unique_uuids = list(set(climb_uuids))
    mapping = {}
    with _connection(database) as connection:
        for chunk in _chunks(unique_uuids):
            placeholders = ", ".join("?" * len(chunk))
            for climb_uuid, angle, display, benchmark, ascensionists, quality in connection.execute(
                f"SELECT climb_uuid, angle, display_difficulty, benchmark_difficulty, "
                f"ascensionist_count, quality_average "
                f"FROM climb_stats WHERE climb_uuid IN ({placeholders})",
                chunk,
            ):
                mapping[(climb_uuid, angle)] = {
                    "display_difficulty": display,
                    "benchmark_difficulty": benchmark,
                    "ascensionist_count": ascensionists,
                    "quality_average": quality,
                }
    return mapping


def get_difficulty_stats_mapping(database, climb_uuids):
    """
    Batch variant of get_difficulty.

    :return: A dictionary mapping (climb_uuid, angle) to
        (display_difficulty, benchmark_difficulty).
    """
    return {
        key: (stats["display_difficulty"], stats["benchmark_difficulty"])
        for key, stats in get_climb_stats_mapping(database, climb_uuids).items()
    }


def get_image_filenames(database):
    with _connection(database) as connection:
        results = connection.execute("SELECT image_filename FROM product_sizes_layouts_sets WHERE image_filename IS NOT NULL")
        return [row[0] for row in results]

def get_layouts_images_dict(database):
    with _connection(database) as connection:
        results = connection.execute(
            """
            SELECT
                l.name layout_name,
                s.name product_size_name,
                p.image_filename
            FROM product_sizes_layouts_sets p
            INNER JOIN
                (SELECT id, name FROM layouts) AS l ON p.layout_id = l.id
            INNER JOIN
                (SELECT id, name FROM product_sizes) AS s ON p.product_size_id = s.id
            WHERE p.image_filename IS NOT NULL;
            """
        )

        layouts_images_dict = collections.defaultdict(list)
        for row in results:
            layouts_images_dict[(row[0],row[1])].append(row[2])
        return layouts_images_dict
