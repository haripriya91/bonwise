-- Dedicated synthetic data schema; never drops existing objects.
CREATE SCHEMA IF NOT EXISTS bonwise_retail_demo;
CREATE TABLE IF NOT EXISTS bonwise_retail_demo.retailers (
 chain text PRIMARY KEY, chain_name text NOT NULL
);
CREATE TABLE IF NOT EXISTS bonwise_retail_demo.stores (
 store_id text PRIMARY KEY, chain text NOT NULL REFERENCES bonwise_retail_demo.retailers,
 store_name text NOT NULL, city text NOT NULL, postcode text NOT NULL,
 country text NOT NULL, is_synthetic boolean NOT NULL CHECK(is_synthetic)
);
CREATE TABLE IF NOT EXISTS bonwise_retail_demo.batches (
 batch_id text PRIMARY KEY, status text NOT NULL CHECK(status IN ('staged','published')),
 source text NOT NULL CHECK(source='demo'), is_synthetic boolean NOT NULL CHECK(is_synthetic)
);
CREATE TABLE IF NOT EXISTS bonwise_retail_demo.raw_offers (
 batch_id text REFERENCES bonwise_retail_demo.batches, offer_id text,
 payload jsonb NOT NULL, PRIMARY KEY(batch_id,offer_id),
 CHECK(payload->>'id'=offer_id)
);
CREATE TABLE IF NOT EXISTS bonwise_retail_demo.offers (
 offer_id text, batch_id text, chain text NOT NULL REFERENCES bonwise_retail_demo.retailers,
 product_name text NOT NULL, brand text, is_organic boolean, category text, variant text,
 pack_quantity numeric(12,3) CHECK(pack_quantity>0), pack_unit text,
 quantity_is_approximate boolean NOT NULL,
 price_eur numeric(12,2) CHECK(price_eur>=0),
 price_basis text NOT NULL CHECK(price_basis IN ('per_pack','per_100g','bundle_total')),
 unit_price_eur numeric(12,2) CHECK(unit_price_eur>=0), unit_price_basis text,
 unit_price_eur_per_kg numeric(12,2), deposit_eur_per_pack numeric(12,2) CHECK(deposit_eur_per_pack>=0), valid_from date, valid_until date,
 multi_buy jsonb, membership_required boolean,
 store_scope text NOT NULL CHECK(store_scope IN ('branch_list','unknown')),
 store_ids jsonb NOT NULL CHECK(jsonb_typeof(store_ids)='array'),
 review_status text NOT NULL CHECK(review_status IN ('approved','needs_review')),
 validation_flags jsonb NOT NULL CHECK(jsonb_typeof(validation_flags)='array'),
 source text NOT NULL CHECK(source='demo'), PRIMARY KEY(batch_id,offer_id),
 FOREIGN KEY(batch_id,offer_id) REFERENCES bonwise_retail_demo.raw_offers,
 CHECK(valid_until IS NULL OR valid_from IS NULL OR valid_until>=valid_from)
);
CREATE INDEX IF NOT EXISTS offers_chain_dates ON bonwise_retail_demo.offers(chain,valid_from,valid_until);
CREATE TABLE IF NOT EXISTS bonwise_retail_demo.price_observations (
 observation_id text PRIMARY KEY, product_code text NOT NULL, product_name text NOT NULL,
 price numeric(12,2) NOT NULL CHECK(price>=0), date date NOT NULL,
 price_is_discounted boolean NOT NULL, receipt_quantity numeric(12,3) NOT NULL CHECK(receipt_quantity>0),
 location_name text NOT NULL, city text NOT NULL, postcode text NOT NULL,
 store_id text NOT NULL REFERENCES bonwise_retail_demo.stores,
 currency text NOT NULL CHECK(currency='EUR'), source text NOT NULL CHECK(source='demo'),
 is_synthetic boolean NOT NULL CHECK(is_synthetic)
);
-- Stable product identity is the bridge between retrieval and exact offer facts.
CREATE TABLE IF NOT EXISTS bonwise_retail_demo.offer_product_links (
 batch_id text NOT NULL, offer_id text NOT NULL,
 product_id uuid NOT NULL REFERENCES public.products(id),
 match_source text NOT NULL CHECK(match_source IN ('reviewed_demo_identity','human_review')),
 PRIMARY KEY(batch_id,offer_id),
 FOREIGN KEY(batch_id,offer_id) REFERENCES bonwise_retail_demo.offers(batch_id,offer_id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS offer_links_product ON bonwise_retail_demo.offer_product_links(product_id);

-- National scope is explicit reviewed source information, never inferred from missing storeId.
ALTER TABLE bonwise_retail_demo.offers ADD COLUMN IF NOT EXISTS country_code text;
ALTER TABLE bonwise_retail_demo.offers ADD COLUMN IF NOT EXISTS national_scope_basis text;
ALTER TABLE bonwise_retail_demo.offers DROP CONSTRAINT IF EXISTS offers_store_scope_check;
ALTER TABLE bonwise_retail_demo.offers ADD CONSTRAINT offers_store_scope_check
 CHECK(store_scope IN ('national','branch_list','unknown'));
ALTER TABLE bonwise_retail_demo.offers DROP CONSTRAINT IF EXISTS offers_national_evidence_check;
ALTER TABLE bonwise_retail_demo.offers ADD CONSTRAINT offers_national_evidence_check CHECK(
 store_scope <> 'national' OR (country_code IS NOT NULL AND country_code='DE'
 AND national_scope_basis IS NOT NULL
 AND national_scope_basis IN ('synthetic_national_fixture_v1','reviewed_source_national')
 AND jsonb_array_length(store_ids)=0));
CREATE OR REPLACE VIEW bonwise_retail_demo.published_approved_offers AS
 SELECT o.* FROM bonwise_retail_demo.offers o JOIN bonwise_retail_demo.batches b USING(batch_id)
 WHERE b.status='published' AND o.review_status='approved';
