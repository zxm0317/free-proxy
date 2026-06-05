import psycopg

db_url = "postgresql://postgres.fhvyahjvaaenayhxbiqw:2ARx%21NTD2L9%21Ciw@aws-1-ap-northeast-1.pooler.supabase.com:5432/postgres"

try:
    with psycopg.connect(db_url) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT key_value FROM config_keys WHERE key_name = 'ADMIN_PASSWORD';")
            row = cur.fetchone()
            if row:
                print(f"ADMIN_PASSWORD: {row[0]}")
            else:
                print("ADMIN_PASSWORD not found in config_keys")
except Exception as e:
    print(f"Error: {e}")
