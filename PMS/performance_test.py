#!/usr/bin/env python3
"""
PMS 성능 테스트 스크립트
네트워크 연결 상태에 따른 성능 개선 효과를 테스트
"""

import asyncio
import time
import logging
from datetime import datetime
from typing import Dict, Any, List
import json
import threading
from concurrent.futures import ThreadPoolExecutor

# 로깅 설정
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


class PerformanceTest:
    """성능 테스트 클래스"""
    
    def __init__(self):
        self.test_results = {}
        self.start_time = None
        self.end_time = None
        
    def log_test_result(self, test_name: str, result: Dict[str, Any]):
        """테스트 결과 로깅"""
        self.test_results[test_name] = result
        logger.info(f"📊 {test_name} 결과: {result}")
    
    async def test_mqtt_publisher_performance(self):
        """MQTT 발행 워커 성능 테스트"""
        logger.info("🚀 MQTT 발행 워커 성능 테스트 시작")
        
        # 테스트용 MQTT 클라이언트 생성
        from pms_app.core.mqtt_client import MQTTClient
        
        config = {
            'broker': 'localhost',
            'port': 1883,
            'client_id': 'test_client',
            'max_publish_workers': 5
        }
        
        mqtt_client = MQTTClient(config)
        
        # 테스트 메시지 생성
        test_messages = []
        for i in range(100):
            test_messages.append({
                'topic': f'test/device_{i % 10}/data',
                'payload': {
                    'device_id': f'device_{i % 10}',
                    'timestamp': datetime.now().isoformat(),
                    'data': {
                        'temperature': 25.5 + i * 0.1,
                        'humidity': 60.0 + i * 0.2,
                        'pressure': 1013.25 + i * 0.05
                    }
                }
            })
        
        # 발행 성능 테스트
        start_time = time.time()
        success_count = 0
        
        for message in test_messages:
            success = mqtt_client.publish(
                message['topic'], 
                message['payload']
            )
            if success:
                success_count += 1
        
        # 워커가 모든 메시지를 처리할 때까지 대기
        await asyncio.sleep(2)
        
        end_time = time.time()
        
        # 통계 수집
        publisher_stats = mqtt_client.publisher.get_stats()
        
        result = {
            'total_messages': len(test_messages),
            'success_count': success_count,
            'total_time': end_time - start_time,
            'messages_per_second': len(test_messages) / (end_time - start_time),
            'publisher_stats': publisher_stats
        }
        
        self.log_test_result("MQTT 발행 워커 성능", result)
        return result
    
    async def test_scheduler_independence(self):
        """스케줄러 독립성 테스트"""
        logger.info("🔄 스케줄러 독립성 테스트 시작")
        
        from pms_app.core.scheduler import PMSScheduler
        
        # 테스트용 가상 장비 핸들러
        class MockDeviceHandler:
            def __init__(self, name: str, delay: float = 0.1):
                self.name = name
                self.poll_interval = 1.0
                self.delay = delay
                self.poll_count = 0
                self.success_count = 0
                
            async def poll_and_publish(self):
                self.poll_count += 1
                await asyncio.sleep(self.delay)
                self.success_count += 1
        
        # 다양한 지연 시간을 가진 가상 장비들
        devices = [
            MockDeviceHandler("device_fast", 0.1),
            MockDeviceHandler("device_slow", 1.0),  # 느린 장비
            MockDeviceHandler("device_normal", 0.3),
            MockDeviceHandler("device_very_slow", 2.0)  # 매우 느린 장비
        ]
        
        # 스케줄러 생성 및 장비 등록
        scheduler = PMSScheduler()
        for device in devices:
            # 타입 체크 우회
            scheduler.add_polling_job(device)  # type: ignore
        
        # 테스트 실행
        start_time = time.time()
        await scheduler.start()
        
        # 5초 동안 실행
        await asyncio.sleep(5)
        
        await scheduler.stop()
        end_time = time.time()
        
        # 결과 수집
        device_results = {}
        for device in devices:
            device_results[device.name] = {
                'poll_count': device.poll_count,
                'success_count': device.success_count,
                'success_rate': device.success_count / device.poll_count if device.poll_count > 0 else 0,
                'delay': device.delay
            }
        
        result = {
            'total_time': end_time - start_time,
            'device_results': device_results,
            'scheduler_stats': scheduler.get_all_stats()
        }
        
        self.log_test_result("스케줄러 독립성", result)
        return result
    
    async def test_polling_publishing_separation(self):
        """폴링과 발행 분리 테스트"""
        logger.info("🔀 폴링과 발행 분리 테스트 시작")
        
        # 테스트용 가상 장비 핸들러
        class MockDeviceHandlerWithFailures:
            def __init__(self, name: str):
                self.name = name
                self.device_type = "test"
                self.poll_count = 0
                self.publish_count = 0
                self.poll_success_count = 0
                self.publish_success_count = 0
                
            async def poll_data(self):
                self.poll_count += 1
                await asyncio.sleep(0.05)  # 폴링 시뮬레이션
                
                # 90% 성공률로 폴링
                if self.poll_count % 10 != 0:
                    self.poll_success_count += 1
                    return {
                        'timestamp': datetime.now().isoformat(),
                        'value': self.poll_count
                    }
                return None
                
            async def publish_data(self, data):
                self.publish_count += 1
                await asyncio.sleep(0.02)  # 발행 시뮬레이션
                
                # 80% 성공률로 발행 (폴링보다 낮음)
                if self.publish_count % 5 != 0:
                    self.publish_success_count += 1
                    return True
                return False
        
        device = MockDeviceHandlerWithFailures("test_device")
        
        # 폴링과 발행 분리 테스트
        start_time = time.time()
        
        # 50회 폴링 및 발행
        for i in range(50):
            # 폴링
            data = await device.poll_data()
            
            # 발행 (폴링 성공 시에만)
            if data:
                await device.publish_data(data)
            
            await asyncio.sleep(0.01)
        
        end_time = time.time()
        
        result = {
            'total_time': end_time - start_time,
            'poll_count': device.poll_count,
            'poll_success_count': device.poll_success_count,
            'poll_success_rate': device.poll_success_count / device.poll_count,
            'publish_count': device.publish_count,
            'publish_success_count': device.publish_success_count,
            'publish_success_rate': device.publish_success_count / device.publish_count if device.publish_count > 0 else 0,
            'operations_per_second': device.poll_count / (end_time - start_time)
        }
        
        self.log_test_result("폴링과 발행 분리", result)
        return result
    
    async def test_parallel_chunk_processing(self):
        """병렬 청크 처리 테스트"""
        logger.info("🚀 병렬 청크 처리 테스트 시작")
        
        # 순차 처리 시뮬레이션
        async def sequential_processing(chunks):
            start_time = time.time()
            results = []
            
            for i, chunk in enumerate(chunks):
                await asyncio.sleep(0.1)  # 네트워크 지연 시뮬레이션
                results.append(f"chunk_{i}_data")
            
            return results, time.time() - start_time
        
        # 병렬 처리 시뮬레이션
        async def parallel_processing(chunks):
            start_time = time.time()
            
            async def process_chunk(chunk_id):
                await asyncio.sleep(0.1)  # 네트워크 지연 시뮬레이션
                return f"chunk_{chunk_id}_data"
            
            # 병렬 처리
            tasks = [process_chunk(i) for i in range(len(chunks))]
            results = await asyncio.gather(*tasks)
            
            return results, time.time() - start_time
        
        # 테스트 청크 생성
        test_chunks = [f"chunk_{i}" for i in range(10)]
        
        # 순차 처리 테스트
        seq_results, seq_time = await sequential_processing(test_chunks)
        
        # 병렬 처리 테스트
        par_results, par_time = await parallel_processing(test_chunks)
        
        result = {
            'chunk_count': len(test_chunks),
            'sequential_time': seq_time,
            'parallel_time': par_time,
            'speedup': seq_time / par_time,
            'sequential_results': len(seq_results),
            'parallel_results': len(par_results),
            'efficiency': (seq_time - par_time) / seq_time * 100
        }
        
        self.log_test_result("병렬 청크 처리", result)
        return result
    
    async def run_all_tests(self):
        """모든 테스트 실행"""
        logger.info("🧪 성능 테스트 시작")
        self.start_time = time.time()
        
        try:
            # 1. MQTT 발행 워커 성능 테스트
            await self.test_mqtt_publisher_performance()
            
            # 2. 스케줄러 독립성 테스트
            await self.test_scheduler_independence()
            
            # 3. 폴링과 발행 분리 테스트
            await self.test_polling_publishing_separation()
            
            # 4. 병렬 청크 처리 테스트
            await self.test_parallel_chunk_processing()
            
        except Exception as e:
            logger.error(f"테스트 중 오류 발생: {e}")
        
        self.end_time = time.time()
        
        # 전체 결과 요약
        self.print_summary()
    
    def print_summary(self):
        """테스트 결과 요약 출력"""
        logger.info("=" * 60)
        logger.info("🎯 성능 테스트 결과 요약")
        logger.info("=" * 60)
        
        total_time = (self.end_time - self.start_time) if (self.start_time and self.end_time) else 0
        logger.info(f"⏱️ 총 테스트 시간: {total_time:.2f}초")
        
        for test_name, result in self.test_results.items():
            logger.info(f"📋 {test_name}:")
            for key, value in result.items():
                if isinstance(value, dict):
                    logger.info(f"   {key}:")
                    for sub_key, sub_value in value.items():
                        logger.info(f"     {sub_key}: {sub_value}")
                else:
                    logger.info(f"   {key}: {value}")
        
        logger.info("=" * 60)
        logger.info("✅ 성능 테스트 완료")
        logger.info("=" * 60)
    
    def save_results(self, filename: str = "performance_test_results.json"):
        """테스트 결과를 파일로 저장"""
        try:
            total_time = (self.end_time - self.start_time) if (self.start_time and self.end_time) else 0
            with open(filename, 'w', encoding='utf-8') as f:
                json.dump({
                    'timestamp': datetime.now().isoformat(),
                    'total_time': total_time,
                    'test_results': self.test_results
                }, f, indent=2, ensure_ascii=False)
            logger.info(f"📄 테스트 결과 저장: {filename}")
        except Exception as e:
            logger.error(f"결과 저장 실패: {e}")


async def main():
    """메인 테스트 실행"""
    test = PerformanceTest()
    await test.run_all_tests()
    test.save_results()


if __name__ == "__main__":
    asyncio.run(main()) 