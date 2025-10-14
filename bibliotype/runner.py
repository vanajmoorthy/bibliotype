from django.test.runner import DiscoverRunner
from django.db import connections
import time


class ForceDisconnectTestRunner(DiscoverRunner):
    """Custom test runner that forces database connections to close."""

    def teardown_databases(self, old_config, **kwargs):
        """Force disconnect all PostgreSQL connections before dropping the test database."""

        # Step 1: Close all Django connections aggressively
        print("🔧 Closing all Django connections...")
        for _ in range(5):
            for conn in connections.all():
                try:
                    if conn.connection is not None:
                        conn.close()
                except Exception as e:
                    print(f"⚠️ Error closing connection: {e}")

            connections.close_all()
            time.sleep(0.2)

        # Step 2: Get the actual test database names from old_config
        print("🔧 Terminating PostgreSQL connections...")

        # old_config is a list of tuples: (connection, old_db_name, serialize)
        for connection, old_db_name, serialize in old_config:
            if connection.vendor != "postgresql":
                continue

            # The actual test database name is in connection.settings_dict['NAME']
            test_db_name = connection.settings_dict["NAME"]
            print(f"🔧 Terminating connections to: {test_db_name}")

            # Close this connection
            connection.close()

            # Create a new connection to postgres database to run terminate command
            from django.db import DEFAULT_DB_ALIAS
            from django.db.backends.postgresql import base

            # Create a temporary connection using the same settings but different DB
            temp_settings = connection.settings_dict.copy()
            temp_settings["NAME"] = "postgres"

            # Create a temporary wrapper
            temp_conn = base.DatabaseWrapper(temp_settings, DEFAULT_DB_ALIAS)

            try:
                temp_conn.ensure_connection()
                with temp_conn.cursor() as cursor:
                    # Check how many connections exist
                    cursor.execute(
                        """
                        SELECT pid, usename, application_name, state
                        FROM pg_stat_activity
                        WHERE datname = %s
                        AND pid <> pg_backend_pid()
                    """,
                        [test_db_name],
                    )

                    active_conns = cursor.fetchall()
                    print(f"🔍 Found {len(active_conns)} active connections to {test_db_name}:")
                    for pid, user, app, state in active_conns:
                        print(f"   - PID {pid}: {user} ({app}) - {state}")

                    # Terminate them
                    cursor.execute(
                        """
                        SELECT pg_terminate_backend(pid)
                        FROM pg_stat_activity
                        WHERE datname = %s
                        AND pid <> pg_backend_pid()
                    """,
                        [test_db_name],
                    )

                    results = cursor.fetchall()
                    terminated = sum(1 for r in results if r[0])
                    print(f"✅ Terminated {terminated} connections")

            except Exception as e:
                print(f"⚠️ Error terminating connections: {e}")
            finally:
                temp_conn.close()

        # Step 3: Final cleanup
        print("🔧 Final cleanup...")
        connections.close_all()
        time.sleep(0.5)

        # Step 4: Proceed with normal teardown
        print("🔧 Proceeding with database destruction...")
        try:
            super().teardown_databases(old_config, **kwargs)
            print("✅ Databases destroyed successfully")
        except Exception as e:
            print(f"❌ Error destroying databases: {e}")
            raise
