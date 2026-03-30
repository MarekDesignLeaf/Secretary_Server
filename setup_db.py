import psycopg2
import os
from psycopg2.extensions import ISOLATION_LEVEL_AUTOCOMMIT

# --- KONFIGURACE (V produkci doporučeno načítat z environmentálních proměnných) ---
DB_PASSWORD = os.getenv("DB_PASS", "Varanus1906")
DB_NAME = os.getenv("DB_NAME", "secretary_db")
DB_USER = os.getenv("DB_USER", "postgres")
DB_HOST = os.getenv("DB_HOST", "localhost")
DB_PORT = os.getenv("DB_PORT", "5432")

def setup():
    conn = None
    try:
        # 1. Fáze: Kontrola existence fyzické databáze
        print(f"--- Fáze 1: Kontrola databáze {DB_NAME} ---")
        admin_conn = psycopg2.connect(
            dbname='postgres',
            user=DB_USER,
            password=DB_PASSWORD,
            host=DB_HOST,
            port=DB_PORT
        )
        admin_conn.set_isolation_level(ISOLATION_LEVEL_AUTOCOMMIT)

        with admin_conn.cursor() as cur:
            cur.execute("SELECT 1 FROM pg_catalog.pg_database WHERE datname = %s", (DB_NAME,))
            if not cur.fetchone():
                # Použití uvozovek pro jméno DB pro případ speciálních znaků
                cur.execute(f'CREATE DATABASE "{DB_NAME}"')
                print(f"✔ Databáze '{DB_NAME}' byla úspěšně vytvořena.")
            else:
                print(f"ℹ Databáze '{DB_NAME}' již existuje. Přeskakuji vytvoření.")
        admin_conn.close()

        # 2. Fáze: Totální vyčištění a reinicializace schématu
        print(f"\n--- Fáze 2: VYČIŠTĚNÍ A REINICIALIZACE schématu 'crm' ---")
        conn = psycopg2.connect(
            dbname=DB_NAME,
            user=DB_USER,
            password=DB_PASSWORD,
            host=DB_HOST,
            port=DB_PORT
        )

        with conn.cursor() as cur:
            # TOTÁLNÍ RESET: Smazání starého schématu
            print("❗ Mažu staré schéma 'crm' a všechna data...")
            cur.execute("DROP SCHEMA IF EXISTS crm CASCADE")

            # Reinicializace
            print("Aplikuji 'schema.sql' pro vytvoření čisté struktury a triggerů...")
            with open('schema.sql', 'r', encoding='utf-8') as f:
                cur.execute(f.read())

            conn.commit()
            print("✔ Schéma, tabulky a auditní triggery byly úspěšně reinicializovány.")

        print("\n[VÝSLEDEK] Infrastruktura Secretary je nyní v ČISTÉM výchozím stavu.")

    except Exception as e:
        if conn:
            conn.rollback()
        print(f"\n❌ KRITICKÁ CHYBA PŘI REINICIALIZACI: {e}")
    finally:
        if conn:
            conn.close()

if __name__ == "__main__":
    setup()
