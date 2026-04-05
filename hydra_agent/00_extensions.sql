-- ============================================================
-- Hydra Agent — Extensions & Shared Setup
-- Run this FIRST before any schema files.
-- ============================================================

-- Enable vector similarity search (BGE-M3 embeddings, 1024 dimensions)
CREATE EXTENSION IF NOT EXISTS vector;

-- Create schemas for logical separation
CREATE SCHEMA IF NOT EXISTS inventory;
CREATE SCHEMA IF NOT EXISTS hr;
CREATE SCHEMA IF NOT EXISTS finance;
