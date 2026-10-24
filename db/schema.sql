

SET statement_timeout = 0;
SET lock_timeout = 0;
SET idle_in_transaction_session_timeout = 0;
SET transaction_timeout = 0;
SET client_encoding = 'UTF8';
SET standard_conforming_strings = on;
SELECT pg_catalog.set_config('search_path', '', false);
SET check_function_bodies = false;
SET xmloption = content;
SET client_min_messages = warning;
SET row_security = off;

ALTER SCHEMA public OWNER TO dnb_owner;

CREATE EXTENSION IF NOT EXISTS pg_trgm WITH SCHEMA public;

COMMENT ON EXTENSION pg_trgm IS 'text similarity measurement and index searching based on trigrams';

SET default_tablespace = '';

SET default_table_access_method = heap;

CREATE TABLE public.entita (
    id bigint NOT NULL,
    libro_id integer NOT NULL,
    tipologia text NOT NULL,
    nome text NOT NULL,
    nome_norm text NOT NULL,
    dati jsonb NOT NULL,
    file text,
    righe int4range,
    revisione text DEFAULT 'automatica'::text NOT NULL,
    adapter text,
    creato_il timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT entita_revisione_check CHECK ((revisione = ANY (ARRAY['automatica'::text, 'verificata'::text, 'scartata'::text]))),
    CONSTRAINT entita_tipologia_check CHECK ((tipologia = ANY (ARRAY['bestiario_new'::text, 'bestiario_old'::text, 'incantesimo'::text, 'classe'::text, 'specie'::text, 'background'::text, 'oggetto'::text, 'regola'::text, 'luogo'::text])))
);

ALTER TABLE public.entita OWNER TO dnb_owner;

CREATE SEQUENCE public.entita_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

ALTER SEQUENCE public.entita_id_seq OWNER TO dnb_owner;

ALTER SEQUENCE public.entita_id_seq OWNED BY public.entita.id;

CREATE TABLE public.libri (
    id integer NOT NULL,
    slug text NOT NULL,
    titolo text NOT NULL,
    anno integer,
    edizione text,
    licenza text NOT NULL,
    pubblico boolean DEFAULT false NOT NULL,
    creato_il timestamp with time zone DEFAULT now() NOT NULL
);

ALTER TABLE public.libri OWNER TO dnb_owner;

COMMENT ON COLUMN public.libri.edizione IS '2014 | 2024 | classico';

COMMENT ON COLUMN public.libri.licenza IS 'CC-BY-4.0 | proprietario';

CREATE VIEW public.entita_pubbliche AS
 SELECT e.id,
    l.slug AS libro,
    l.titolo AS libro_titolo,
    l.edizione,
    e.tipologia,
    e.nome,
    e.nome_norm,
    e.dati,
    e.file,
    e.righe,
    e.revisione
   FROM (public.entita e
     JOIN public.libri l ON ((l.id = e.libro_id)))
  WHERE (l.pubblico AND (e.revisione <> 'scartata'::text));

ALTER VIEW public.entita_pubbliche OWNER TO dnb_owner;

CREATE SEQUENCE public.libri_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

ALTER SEQUENCE public.libri_id_seq OWNER TO dnb_owner;

ALTER SEQUENCE public.libri_id_seq OWNED BY public.libri.id;

ALTER TABLE ONLY public.entita ALTER COLUMN id SET DEFAULT nextval('public.entita_id_seq'::regclass);

ALTER TABLE ONLY public.libri ALTER COLUMN id SET DEFAULT nextval('public.libri_id_seq'::regclass);

ALTER TABLE ONLY public.entita
    ADD CONSTRAINT entita_libro_id_tipologia_nome_norm_key UNIQUE (libro_id, tipologia, nome_norm);

ALTER TABLE ONLY public.entita
    ADD CONSTRAINT entita_pkey PRIMARY KEY (id);

ALTER TABLE ONLY public.libri
    ADD CONSTRAINT libri_pkey PRIMARY KEY (id);

ALTER TABLE ONLY public.libri
    ADD CONSTRAINT libri_slug_key UNIQUE (slug);

CREATE INDEX entita_dati_gin ON public.entita USING gin (dati);

CREATE INDEX entita_fts ON public.entita USING gin (to_tsvector('italian'::regconfig, ((nome || ' '::text) || COALESCE((dati ->> 'descrizione'::text), ''::text))));

CREATE INDEX entita_nome_trgm ON public.entita USING gin (nome public.gin_trgm_ops);

CREATE INDEX entita_tipo ON public.entita USING btree (tipologia, libro_id);

ALTER TABLE ONLY public.entita
    ADD CONSTRAINT entita_libro_id_fkey FOREIGN KEY (libro_id) REFERENCES public.libri(id) ON DELETE CASCADE;

REVOKE USAGE ON SCHEMA public FROM PUBLIC;
GRANT USAGE ON SCHEMA public TO dnb_loader;
GRANT USAGE ON SCHEMA public TO dnb_web;

GRANT SELECT,INSERT,DELETE,UPDATE ON TABLE public.entita TO dnb_loader;

GRANT SELECT,USAGE ON SEQUENCE public.entita_id_seq TO dnb_loader;

GRANT SELECT,INSERT,DELETE,UPDATE ON TABLE public.libri TO dnb_loader;

GRANT SELECT ON TABLE public.entita_pubbliche TO dnb_web;

GRANT SELECT,USAGE ON SEQUENCE public.libri_id_seq TO dnb_loader;


