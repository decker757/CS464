-- Runs once, on first initialisation of the Postgres volume.
-- Gives the test suite its own database so it can truncate freely.
CREATE DATABASE cs464_test;
