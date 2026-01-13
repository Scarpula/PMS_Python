"""
시스템 모니터링 및 자동 복구 모듈
메모리 누수, 블로킹, 데드락 감지 및 자동 복구 시스템
"""

import asyncio
import threading
import time
import psutil
import gc
import logging
from typing import Dict, Any, Optional, List, Callable
from datetime import datetime, timedelta
from dataclasses import dataclass
from enum import Enum
import traceback
import signal
import sys


class HealthStatus(Enum):
    """시스템 건강 상태"""
    HEALTHY = "healthy"
    WARNING = "warning"
    CRITICAL = "critical"
    EMERGENCY = "emergency"


@dataclass
class SystemMetrics:
    """시스템 메트릭 정보"""
    timestamp: datetime
    memory_usage_mb: float
    memory_percent: float
    cpu_usage_percent: float
    thread_count: int
    fd_count: int
    queue_sizes: Dict[str, int]
    active_tasks: int
    health_status: HealthStatus
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            'timestamp': self.timestamp.isoformat(),
            'memory_usage_mb': self.memory_usage_mb,
            'memory_percent': self.memory_percent,
            'cpu_usage_percent': self.cpu_usage_percent,
            'thread_count': self.thread_count,
            'fd_count': self.fd_count,
            'queue_sizes': self.queue_sizes,
            'active_tasks': self.active_tasks,
            'health_status': self.health_status.value
        }


class SystemMonitor:
    """시스템 모니터링 및 자동 복구 클래스"""
    
    def __init__(self, config: Dict[str, Any]):
        self.config = config
        self.logger = logging.getLogger(self.__class__.__name__)
        
        # 모니터링 설정
        self.monitoring_enabled = config.get('monitoring', {}).get('enabled', True)
        self.check_interval = config.get('monitoring', {}).get('check_interval', 10)  # 10초
        self.memory_threshold_mb = config.get('monitoring', {}).get('memory_threshold_mb', 500)
        self.cpu_threshold_percent = config.get('monitoring', {}).get('cpu_threshold_percent', 80)
        self.thread_threshold = config.get('monitoring', {}).get('thread_threshold', 50)
        
        # 자동 복구 설정
        self.auto_recovery_enabled = config.get('recovery', {}).get('enabled', True)
        self.max_recovery_attempts = config.get('recovery', {}).get('max_attempts', 3)
        self.recovery_cooldown = config.get('recovery', {}).get('cooldown_seconds', 60)
        
        # 상태 관리
        self.running = False
        self.monitor_task: Optional[asyncio.Task] = None
        self.current_metrics: Optional[SystemMetrics] = None
        self.metrics_history: List[SystemMetrics] = []
        self.max_history_size = 100
        
        # 복구 관리
        self.recovery_attempts = 0
        self.last_recovery_time: Optional[datetime] = None
        self.recovery_callbacks: List[Callable] = []
        
        # 구성 요소 참조
        self.scheduler = None
        self.mqtt_client = None
        self.device_handlers = []
        self.data_manager = None
        
        # 블로킹 감지
        self.last_activity_time = datetime.now()
        self.activity_timeout = timedelta(seconds=30)  # 30초 동안 활동 없으면 블로킹 의심
        
        # 메모리 누수 감지
        self.memory_baseline = 0
        self.memory_growth_threshold = 100  # MB
        self.memory_samples = []
        self.max_memory_samples = 10
        
        # 긴급 복구 핸들러
        self.emergency_handler = None
        
        self.logger.info("🔍 시스템 모니터링 및 자동 복구 시스템 초기화 완료")
    
    def set_components(self, scheduler, mqtt_client, device_handlers, data_manager):
        """시스템 구성 요소 설정"""
        self.scheduler = scheduler
        self.mqtt_client = mqtt_client
        self.device_handlers = device_handlers
        self.data_manager = data_manager
        
        # 메모리 베이스라인 설정
        self.memory_baseline = psutil.Process().memory_info().rss / 1024 / 1024
        self.logger.info(f"📊 메모리 베이스라인 설정: {self.memory_baseline:.1f}MB")
    
    def add_recovery_callback(self, callback: Callable):
        """복구 콜백 함수 추가"""
        self.recovery_callbacks.append(callback)
    
    def set_emergency_handler(self, handler: Callable):
        """긴급 복구 핸들러 설정"""
        self.emergency_handler = handler
    
    async def start(self):
        """모니터링 시작"""
        if not self.monitoring_enabled:
            self.logger.info("⚠️ 시스템 모니터링이 비활성화되어 있습니다")
            return
        
        if self.running:
            self.logger.warning("⚠️ 시스템 모니터링이 이미 실행 중입니다")
            return
        
        self.running = True
        self.monitor_task = asyncio.create_task(self._monitoring_loop())
        
        # SIGINT 핸들러 등록 (긴급 복구용) - PyInstaller 환경 고려
        try:
            signal.signal(signal.SIGINT, self._signal_handler)
            self.logger.info("✅ SIGINT 핸들러 등록 완료")
        except ValueError as e:
            self.logger.warning(f"⚠️ SIGINT 핸들러 등록 실패 (PyInstaller 환경): {e}")
            self.logger.info("ℹ️ PyInstaller 환경에서는 시그널 핸들러가 제한될 수 있습니다")
        
        self.logger.info("🚀 시스템 모니터링 시작")
    
    async def stop(self):
        """모니터링 중지"""
        if not self.running:
            return
        
        self.running = False
        
        if self.monitor_task:
            self.monitor_task.cancel()
            try:
                await self.monitor_task
            except asyncio.CancelledError:
                pass
        
        self.logger.info("🛑 시스템 모니터링 중지")
    
    def _signal_handler(self, signum, frame):
        """시그널 핸들러 (SIGINT 처리)"""
        self.logger.warning(f"⚠️ 시그널 수신: {signum}")
        
        if signum == signal.SIGINT:
            self.logger.error("🚨 SIGINT 시그널 감지 - 시스템 블로킹 또는 데드락 가능성")
            self.logger.error("🔧 긴급 복구 시도...")
            
            # 긴급 복구 실행
            asyncio.create_task(self._emergency_recovery())
    
    async def _monitoring_loop(self):
        """모니터링 루프"""
        self.logger.info("🔄 시스템 모니터링 루프 시작")
        
        while self.running:
            try:
                # 시스템 메트릭 수집
                metrics = await self._collect_metrics()
                self.current_metrics = metrics
                
                # 메트릭 히스토리 업데이트
                self._update_metrics_history(metrics)
                
                # 건강 상태 평가
                health_status = self._evaluate_health(metrics)
                
                # 자동 복구 판단
                if self.auto_recovery_enabled:
                    await self._check_auto_recovery(health_status, metrics)
                
                # 활동 시간 업데이트
                self.last_activity_time = datetime.now()
                
                # 다음 체크까지 대기
                await asyncio.sleep(self.check_interval)
                
            except asyncio.CancelledError:
                break
            except Exception as e:
                self.logger.error(f"❌ 모니터링 루프 오류: {e}")
                self.logger.error(f"📍 스택 트레이스:\n{traceback.format_exc()}")
                await asyncio.sleep(5)
        
        self.logger.info("🛑 시스템 모니터링 루프 종료")
    
    async def _collect_metrics(self) -> SystemMetrics:
        """시스템 메트릭 수집"""
        try:
            process = psutil.Process()
            
            # 메모리 사용량
            memory_info = process.memory_info()
            memory_mb = memory_info.rss / 1024 / 1024
            memory_percent = process.memory_percent()
            
            # CPU 사용량
            cpu_percent = process.cpu_percent(interval=0.1)
            
            # 스레드 수
            thread_count = process.num_threads()
            
            # 파일 디스크립터 수 (리눅스에서만 지원)
            try:
                fd_count = getattr(process, 'num_fds', lambda: 0)()
            except (AttributeError, psutil.AccessDenied):
                fd_count = 0
            
            # 큐 크기 (MQTT 발행 큐 등)
            queue_sizes = {}
            if self.mqtt_client and hasattr(self.mqtt_client, 'publisher'):
                queue_sizes['mqtt_publish'] = self.mqtt_client.publisher.publish_queue.qsize()
            
            # 활성 태스크 수
            active_tasks = len([t for t in asyncio.all_tasks() if not t.done()])
            
            # 메모리 누수 감지
            self.memory_samples.append(memory_mb)
            if len(self.memory_samples) > self.max_memory_samples:
                self.memory_samples.pop(0)
            
            # 건강 상태 초기 평가
            health_status = HealthStatus.HEALTHY
            
            return SystemMetrics(
                timestamp=datetime.now(),
                memory_usage_mb=memory_mb,
                memory_percent=memory_percent,
                cpu_usage_percent=cpu_percent,
                thread_count=thread_count,
                fd_count=fd_count,
                queue_sizes=queue_sizes,
                active_tasks=active_tasks,
                health_status=health_status
            )
            
        except Exception as e:
            self.logger.error(f"❌ 메트릭 수집 오류: {e}")
            return SystemMetrics(
                timestamp=datetime.now(),
                memory_usage_mb=0, memory_percent=0, cpu_usage_percent=0,
                thread_count=0, fd_count=0, queue_sizes={}, active_tasks=0,
                health_status=HealthStatus.CRITICAL
            )
    
    def _update_metrics_history(self, metrics: SystemMetrics):
        """메트릭 히스토리 업데이트"""
        self.metrics_history.append(metrics)
        
        # 히스토리 크기 제한
        if len(self.metrics_history) > self.max_history_size:
            self.metrics_history.pop(0)
    
    def _evaluate_health(self, metrics: SystemMetrics) -> HealthStatus:
        """건강 상태 평가"""
        issues = []
        
        # 메모리 사용량 체크
        if metrics.memory_usage_mb > self.memory_threshold_mb:
            issues.append(f"메모리 사용량 초과: {metrics.memory_usage_mb:.1f}MB > {self.memory_threshold_mb}MB")
        
        # 메모리 누수 체크
        if self._detect_memory_leak():
            issues.append("메모리 누수 감지")
        
        # CPU 사용량 체크
        if metrics.cpu_usage_percent > self.cpu_threshold_percent:
            issues.append(f"CPU 사용량 초과: {metrics.cpu_usage_percent:.1f}% > {self.cpu_threshold_percent}%")
        
        # 스레드 수 체크
        if metrics.thread_count > self.thread_threshold:
            issues.append(f"스레드 수 초과: {metrics.thread_count} > {self.thread_threshold}")
        
        # 블로킹 감지
        if self._detect_blocking():
            issues.append("시스템 블로킹 감지")
        
        # 큐 백로그 체크
        for queue_name, size in metrics.queue_sizes.items():
            if size > 100:  # 큐 크기 임계값
                issues.append(f"큐 백로그: {queue_name} = {size}")
        
        # 건강 상태 결정
        if not issues:
            status = HealthStatus.HEALTHY
        elif len(issues) == 1 and "메모리" not in issues[0]:
            status = HealthStatus.WARNING
        elif len(issues) <= 2:
            status = HealthStatus.CRITICAL
        else:
            status = HealthStatus.EMERGENCY
        
        # 문제 로깅
        if issues:
            self.logger.warning(f"⚠️ 시스템 건강 상태: {status.value}")
            for issue in issues:
                self.logger.warning(f"   - {issue}")
        
        metrics.health_status = status
        return status
    
    def _detect_memory_leak(self) -> bool:
        """메모리 누수 감지"""
        if len(self.memory_samples) < 5:
            return False
        
        # 최근 5개 샘플의 평균 증가율 계산
        recent_samples = self.memory_samples[-5:]
        if len(recent_samples) < 2:
            return False
        
        growth = recent_samples[-1] - recent_samples[0]
        growth_rate = growth / len(recent_samples)
        
        # 메모리 증가율이 임계값을 초과하는지 확인
        return growth_rate > 10  # 10MB/sample 이상 증가
    
    def _detect_blocking(self) -> bool:
        """블로킹 감지"""
        current_time = datetime.now()
        time_since_activity = current_time - self.last_activity_time
        
        return time_since_activity > self.activity_timeout
    
    async def _check_auto_recovery(self, health_status: HealthStatus, metrics: SystemMetrics):
        """자동 복구 체크"""
        if health_status in [HealthStatus.CRITICAL, HealthStatus.EMERGENCY]:
            # 복구 쿨다운 체크
            if self.last_recovery_time:
                time_since_recovery = datetime.now() - self.last_recovery_time
                if time_since_recovery.total_seconds() < self.recovery_cooldown:
                    return
            
            # 최대 복구 시도 횟수 체크
            if self.recovery_attempts >= self.max_recovery_attempts:
                self.logger.error("🚨 최대 복구 시도 횟수 초과 - 긴급 복구 실행")
                await self._emergency_recovery()
                return
            
            # 자동 복구 실행
            await self._auto_recovery(health_status, metrics)
    
    async def _auto_recovery(self, health_status: HealthStatus, metrics: SystemMetrics):
        """자동 복구 실행"""
        self.logger.warning(f"🔧 자동 복구 시작 (시도 {self.recovery_attempts + 1}/{self.max_recovery_attempts})")

        try:
            # 가비지 컬렉션 강제 실행
            collected = gc.collect()
            self.logger.info(f"🧹 가비지 컬렉션: {collected}개 객체 정리")

            # MQTT 발행 큐 정리
            if self.mqtt_client and hasattr(self.mqtt_client, 'publisher'):
                queue_size = self.mqtt_client.publisher.publish_queue.qsize()
                if queue_size > 50:
                    self.logger.warning(f"📤 MQTT 큐 정리 중: {queue_size}개 메시지")
                    # 큐 크기 제한 (오래된 메시지 제거)
                    while self.mqtt_client.publisher.publish_queue.qsize() > 50:
                        try:
                            self.mqtt_client.publisher.publish_queue.get_nowait()
                        except:
                            break

            # 장비 핸들러 연결 상태 확인 및 복구
            if self.device_handlers:
                for handler in self.device_handlers:
                    try:
                        if hasattr(handler, 'connection_pool') and handler.connection_pool:
                            await handler.connection_pool.close_all()
                            await handler.connection_pool.initialize()
                            self.logger.info(f"🔄 장비 연결 풀 재초기화: {handler.name}")
                    except Exception as e:
                        self.logger.error(f"❌ 장비 연결 복구 실패: {handler.name} - {e}")

            # 복구 콜백 실행
            for callback in self.recovery_callbacks:
                try:
                    await callback()
                except Exception as e:
                    self.logger.error(f"❌ 복구 콜백 실행 오류: {e}")

            # 복구 상태 업데이트
            self.recovery_attempts += 1
            self.last_recovery_time = datetime.now()

            self.logger.info("✅ 자동 복구 완료")

            # 복구 성공 시 일정 시간 후 복구 시도 횟수 리셋
            await asyncio.sleep(self.recovery_cooldown)
            if self.recovery_attempts > 0:
                self.logger.info(f"🔄 복구 시도 횟수 리셋 (현재: {self.recovery_attempts} → 0)")
                self.recovery_attempts = 0

        except Exception as e:
            self.logger.error(f"❌ 자동 복구 실패: {e}")
            self.logger.error(f"📍 스택 트레이스:\n{traceback.format_exc()}")
    
    async def _emergency_recovery(self):
        """긴급 복구 실행"""
        self.logger.error("🚨 긴급 복구 시스템 활성화")

        try:
            # 긴급 복구 핸들러 실행
            if self.emergency_handler:
                await self.emergency_handler()

            # 시스템 재시작 (선택사항)
            if self.config.get('recovery', {}).get('restart_on_emergency', False):
                # 모든 태스크 강제 종료
                tasks = [t for t in asyncio.all_tasks() if not t.done() and t != asyncio.current_task()]
                if tasks:
                    self.logger.warning(f"⚠️ {len(tasks)}개 태스크 강제 종료")
                    for task in tasks:
                        task.cancel()

                    # 태스크 종료 대기
                    await asyncio.gather(*tasks, return_exceptions=True)

                self.logger.error("🔄 시스템 재시작 중...")
                sys.exit(1)
            else:
                self.logger.warning("⚠️ 긴급 복구 완료 - 태스크는 계속 실행됩니다")
                self.logger.warning("⚠️ restart_on_emergency=false 설정으로 시스템은 계속 동작합니다")

        except Exception as e:
            self.logger.error(f"❌ 긴급 복구 실행 오류: {e}")
            self.logger.error(f"📍 스택 트레이스:\n{traceback.format_exc()}")
    
    def get_current_metrics(self) -> Optional[SystemMetrics]:
        """현재 메트릭 조회"""
        return self.current_metrics
    
    def get_metrics_history(self) -> List[SystemMetrics]:
        """메트릭 히스토리 조회"""
        return self.metrics_history.copy()
    
    def get_health_report(self) -> Dict[str, Any]:
        """건강 상태 리포트 생성"""
        if not self.current_metrics:
            return {'status': 'not_available'}
        
        return {
            'status': self.current_metrics.health_status.value,
            'metrics': self.current_metrics.to_dict(),
            'recovery_attempts': self.recovery_attempts,
            'last_recovery': self.last_recovery_time.isoformat() if self.last_recovery_time else None,
            'memory_baseline': self.memory_baseline,
            'memory_growth': self.current_metrics.memory_usage_mb - self.memory_baseline,
            'monitoring_enabled': self.monitoring_enabled,
            'auto_recovery_enabled': self.auto_recovery_enabled
        }
    
    def force_recovery(self):
        """강제 복구 트리거"""
        if self.current_metrics:
            self.logger.warning("🔧 강제 복구 트리거")
            asyncio.create_task(self._auto_recovery(HealthStatus.CRITICAL, self.current_metrics))
    
    def reset_recovery_attempts(self):
        """복구 시도 횟수 리셋"""
        self.recovery_attempts = 0
        self.last_recovery_time = None
        self.logger.info("🔄 복구 시도 횟수 리셋") 