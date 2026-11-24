import asyncio
import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from app.core.performance import PerformanceMiddleware, Timings, _current, measure, instrument_engine

class TimingTests(unittest.TestCase):
    def test_sync_route_metrics_cross_worker_context_without_payloads(self):
        app=FastAPI();app.add_middleware(PerformanceMiddleware)
        @app.get('/test')
        def route():
            with measure('auth'):pass
            return {'secret':'not-in-header'}
        response=TestClient(app).get('/test',headers={'Authorization':'Bearer not-in-header'})
        self.assertEqual(response.status_code,200)
        header=response.headers['server-timing'];self.assertIn('auth_count;desc="1"',header)
        self.assertNotIn('secret',header);self.assertNotIn('not-in-header',header);self.assertIsNone(_current.get())
    def test_queries_and_failed_queries_count_once(self):
        engine=instrument_engine(create_engine('sqlite://'));self.assertIs(instrument_engine(engine),engine)
        stats=Timings();token=_current.set(stats)
        try:
            with engine.connect() as conn:
                conn.execute(text('select 1')).all()
                with self.assertRaises(Exception):conn.execute(text('select not_a_column'))
            self.assertEqual(stats.values['db_query'][1],2);self.assertEqual(stats.values['db_connect'][1],1)
        finally:_current.reset(token);engine.dispose()
    def test_connection_error_is_measured(self):
        engine=instrument_engine(create_engine('sqlite://'));stats=Timings();token=_current.set(stats)
        try:
            with patch.object(engine.dialect,'connect',side_effect=RuntimeError('private-host')):
                with self.assertRaises(RuntimeError):engine.connect()
            self.assertEqual(stats.values['db_connect'][1],1);self.assertNotIn('private-host',stats.header(1))
        finally:_current.reset(token);engine.dispose()
    def test_concurrent_requests_do_not_mix(self):
        async def run(n):
            stats=Timings();token=_current.set(stats)
            try:
                for _ in range(n):
                    with measure('auth'):await asyncio.sleep(0)
                return stats.values['auth'][1]
            finally:_current.reset(token)
        async def both():return await asyncio.gather(run(2),run(5))
        self.assertEqual(asyncio.run(both()),[2,5])
    def test_threaded_writes_are_safe(self):
        stats=Timings()
        with ThreadPoolExecutor(4) as pool:list(pool.map(lambda _:stats.add('auth',1),range(100)))
        self.assertEqual(stats.values['auth'],(100,100))
    def test_model_and_retrieval_stage_timings(self):
        from types import SimpleNamespace
        from app.ai.analysis_tracing import AnalysisTrace
        stats=Timings();token=_current.set(stats)
        try:
            with patch('app.ai.analysis_tracing.get_settings',return_value=SimpleNamespace(embedding_model='test')):
                trace=AnalysisTrace('test','test')
                with trace.span('analysis_question_routing'):pass
                with trace.span('retrieve_product_candidates'):pass
            self.assertEqual(stats.values['ai_stage'][1],1)
            self.assertEqual(stats.values['retrieval'][1],1)
        finally:_current.reset(token)
    def test_no_request_is_noop(self):
        with measure('auth'):pass
        self.assertIsNone(_current.get())
    def test_fixed_metric_names_only(self):
        with self.assertRaises(ValueError):Timings().add('private-record',1)

class DatabaseErrorTests(unittest.TestCase):
    def test_connection_timeout_returns_safe_503_with_timings_and_cors(self):
        from app.core.errors import database_unavailable
        from fastapi.middleware.cors import CORSMiddleware
        from sqlalchemy.exc import OperationalError
        from psycopg.errors import ConnectionTimeout
        app=FastAPI();app.add_exception_handler(OperationalError,database_unavailable)
        app.add_middleware(CORSMiddleware,allow_origins=['https://example.test'],expose_headers=['Server-Timing'])
        app.add_middleware(PerformanceMiddleware)
        @app.get('/households/test')
        def failed():
            with measure('db_connect'):raise OperationalError('private-sql',{'token':'private-value'},ConnectionTimeout('private-host'))
        response=TestClient(app).get('/households/test',headers={'Origin':'https://example.test'})
        self.assertEqual(response.status_code,503);self.assertEqual(response.headers['retry-after'],'5')
        self.assertIn('db_connect',response.headers['server-timing']);self.assertIn('Server-Timing',response.headers['access-control-expose-headers'])
        self.assertNotIn('private',response.text);self.assertEqual(response.headers['cache-control'],'no-store')
    def test_other_operational_errors_are_not_mislabeled_as_transient(self):
        from app.core.errors import database_unavailable
        from sqlalchemy.exc import OperationalError
        app=FastAPI();app.add_exception_handler(OperationalError,database_unavailable)
        @app.get('/test')
        def failed():raise OperationalError('private-sql',{},ValueError('private'))
        response=TestClient(app).get('/test');self.assertEqual(response.status_code,500);self.assertNotIn('retry-after',response.headers);self.assertNotIn('private',response.text)
