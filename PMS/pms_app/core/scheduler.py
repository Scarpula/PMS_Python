"""
스케줄러 모듈
장비 핸들러들의 주기적인 폴링 작업을 독립적인 태스크로 관리하는 스케줄러
"""

import asyncio
import logging
from typing import TYPE_CHECKING, Dict, Any, Optional
import time
import threading
from datetime import datetime
from dataclasses import dataclass, field

if TYPE_CHECKING:
    from ..devices.base import DeviceInterface


@dataclass
class DeviceTask:
    """장비 태스크 정보"""
    device_handler: 'DeviceInterface'
    task: Optional[asyncio.Task]
    name: str
    poll_interval: float
    last_execution: datetime = field(default_factory=datetime.now)
    total_executions: int = 0
    successful_executions: int = 0
    failed_executions: int = 0
    last_error: str = ""
    consecutive_errors: int = 0
    max_consecutive_errors: int = 5
    timeout_seconds: float = 10.0
    is_healthy: bool = True
    
    def update_success(self):
        """성공 시 통계 업데이트"""
        self.last_execution = datetime.now()
        self.total_executions += 1
        self.successful_executions += 1
        self.consecutive_errors = 0
        self.last_error = ""
        self.is_healthy = True
    
    def update_failure(self, error_msg: str):
        """실패 시 통계 업데이트"""
        self.last_execution = datetime.now()
        self.total_executions += 1
        self.failed_executions += 1
        self.consecutive_errors += 1
        self.last_error = error_msg
        
        # 연속 실패 횟수에 따른 건강 상태 업데이트
        if self.consecutive_errors >= self.max_consecutive_errors:
            self.is_healthy = False
    
    def get_success_rate(self) -> float:
        """성공률 계산"""
        if self.total_executions == 0:
            return 0.0
        return (self.successful_executions / self.total_executions) * 100
    
    def get_stats(self) -> Dict[str, Any]:
        """통계 정보 반환"""
        return {
            'name': self.name,
            'poll_interval': self.poll_interval,
            'last_execution': self.last_execution.isoformat(),
            'total_executions': self.total_executions,
            'successful_executions': self.successful_executions,
            'failed_executions': self.failed_executions,
            'success_rate': self.get_success_rate(),
            'consecutive_errors': self.consecutive_errors,
            'last_error': self.last_error,
            'is_healthy': self.is_healthy,
            'timeout_seconds': self.timeout_seconds
        }


class PMSScheduler:
    """PMS 스케줄러 클래스 - 장비별 독립적 태스크 관리"""
    
    def __init__(self):
        """스케줄러 초기화"""
        self.logger = logging.getLogger(self.__class__.__name__)
        self.running = False
        
        # 장비별 태스크 관리
        self.device_tasks: Dict[str, DeviceTask] = {}
        self.task_lock = threading.Lock()
        
        # 통계 및 모니터링
        self.start_time: Optional[datetime] = None
        self.total_tasks_created = 0
        self.total_tasks_failed = 0
        
        # 건강성 체크
        self.health_check_task: Optional[asyncio.Task] = None
        self.health_check_interval = 30  # 30초마다 건강성 체크
        
        self.logger.info("🚀 개선된 PMS 스케줄러 초기화 완료 - 장비별 독립적 태스크 관리")
    
    def add_polling_job(self, device_handler: 'DeviceInterface'):
        """
        장비 핸들러를 독립적인 태스크로 추가
        
        Args:
            device_handler: 장비 핸들러 인스턴스
        """
        device_name = device_handler.name
        
        with self.task_lock:
            # 기존 태스크가 있으면 제거
            if device_name in self.device_tasks:
                self.remove_polling_job(device_name)
            
            # 장비별 적응형 타임아웃 설정
            adaptive_timeout = min(15, max(5, device_handler.poll_interval * 2))
            
            # 장비 태스크 생성
            device_task = DeviceTask(
                device_handler=device_handler,
                task=None,  # 나중에 설정
                name=device_name,
                poll_interval=device_handler.poll_interval,
                timeout_seconds=adaptive_timeout
            )
            
            self.device_tasks[device_name] = device_task
            self.total_tasks_created += 1
            
            self.logger.info(f"✅ 장비 태스크 등록: {device_name} (주기: {device_handler.poll_interval}초, 타임아웃: {adaptive_timeout}초)")
    
    def remove_polling_job(self, device_name: str):
        """
        장비 태스크 제거
        
        Args:
            device_name: 제거할 장비 이름
        """
        with self.task_lock:
            if device_name in self.device_tasks:
                device_task = self.device_tasks[device_name]
                
                # 태스크 취소
                if device_task.task and not device_task.task.done():
                    device_task.task.cancel()
                    self.logger.info(f"🛑 장비 태스크 취소: {device_name}")
                
                # 딕셔너리에서 제거
                del self.device_tasks[device_name]
                self.logger.info(f"✅ 장비 태스크 제거 완료: {device_name}")
    
    async def start(self):
        """스케줄러 시작"""
        if self.running:
            self.logger.warning("⚠️ 스케줄러가 이미 실행 중입니다")
            return
        
        self.running = True
        self.start_time = datetime.now()
        
        # 모든 장비 태스크 시작
        with self.task_lock:
            for device_name, device_task in self.device_tasks.items():
                if device_task.task is None or device_task.task.done():
                    device_task.task = asyncio.create_task(
                        self._device_polling_loop(device_task),
                        name=f"DevicePolling-{device_name}"
                    )
                    self.logger.info(f"🔄 장비 폴링 태스크 시작: {device_name}")
        
        # 건강성 체크 태스크 시작
        if self.health_check_task is None or self.health_check_task.done():
            self.health_check_task = asyncio.create_task(
                self._health_check_loop(),
                name="SchedulerHealthCheck"
            )
            self.logger.info("🩺 스케줄러 건강성 체크 태스크 시작")
        
        self.logger.info(f"✅ 스케줄러 시작 완료 - {len(self.device_tasks)}개 장비 태스크 실행 중")
    
    async def stop(self):
        """스케줄러 정지"""
        if not self.running:
            return
        
        self.running = False
        
        # 건강성 체크 태스크 정지
        if self.health_check_task and not self.health_check_task.done():
            self.health_check_task.cancel()
            try:
                await self.health_check_task
            except asyncio.CancelledError:
                pass
        
        # 모든 장비 태스크 정지
        with self.task_lock:
            cancel_tasks = []
            for device_name, device_task in self.device_tasks.items():
                if device_task.task and not device_task.task.done():
                    device_task.task.cancel()
                    cancel_tasks.append((device_name, device_task.task))
            
            # 태스크 취소 완료 대기
            for device_name, task in cancel_tasks:
                try:
                    await asyncio.wait_for(task, timeout=5.0)
                except (asyncio.CancelledError, asyncio.TimeoutError):
                    self.logger.warning(f"⚠️ 장비 태스크 강제 종료: {device_name}")
        
        self.logger.info("🛑 스케줄러 정지 완료")
    
    async def _device_polling_loop(self, device_task: DeviceTask):
        """
        장비별 독립적 폴링 루프
        
        Args:
            device_task: 장비 태스크 정보
        """
        device_name = device_task.name
        device_handler = device_task.device_handler
        
        self.logger.info(f"🔄 장비 폴링 루프 시작: {device_name}")
        
        while self.running:
            try:
                # 폴링 실행
                start_time = time.time()
                
                try:
                    # 타임아웃 적용하여 폴링 실행
                    await asyncio.wait_for(
                        device_handler.poll_and_publish(),
                        timeout=device_task.timeout_seconds
                    )
                    
                    # 성공 시 통계 업데이트
                    device_task.update_success()
                    
                    # 실행 시간 로깅
                    execution_time = time.time() - start_time
                    if execution_time > device_task.poll_interval:
                        self.logger.warning(
                            f"⚠️ 폴링 시간 초과: {device_name} - {execution_time:.2f}초 "
                            f"(주기: {device_task.poll_interval}초)"
                        )
                    
                except asyncio.TimeoutError:
                    # 타임아웃 처리
                    execution_time = time.time() - start_time
                    error_msg = f"폴링 타임아웃 ({execution_time:.2f}초)"
                    device_task.update_failure(error_msg)
                    
                    self.logger.error(
                        f"⚠️ 장비 폴링 타임아웃: {device_name} - {execution_time:.2f}초 "
                        f"(제한: {device_task.timeout_seconds}초)"
                    )
                    
                except Exception as e:
                    # 일반 예외 처리
                    execution_time = time.time() - start_time
                    error_msg = f"폴링 오류: {str(e)}"
                    device_task.update_failure(error_msg)
                    
                    self.logger.error(
                        f"❌ 장비 폴링 오류: {device_name} - {e} (시간: {execution_time:.2f}초)"
                    )
                
                # 연속 실패 시 회복 시간 증가
                if device_task.consecutive_errors > 0:
                    recovery_delay = min(device_task.consecutive_errors * 2, 30)
                    self.logger.info(f"⏰ 연속 오류 회복 대기: {device_name} - {recovery_delay}초")
                    await asyncio.sleep(recovery_delay)
                    continue
                
                # 다음 폴링까지 대기
                await asyncio.sleep(device_task.poll_interval)
                
            except asyncio.CancelledError:
                self.logger.info(f"🛑 장비 폴링 루프 취소: {device_name}")
                break
                
            except Exception as e:
                self.logger.error(f"❌ 장비 폴링 루프 예외: {device_name} - {e}")
                device_task.update_failure(f"루프 예외: {str(e)}")
                await asyncio.sleep(5)  # 5초 대기 후 재시도
        
        self.logger.info(f"🛑 장비 폴링 루프 종료: {device_name}")
    
    async def _health_check_loop(self):
        """스케줄러 건강성 체크 루프"""
        self.logger.info("🩺 스케줄러 건강성 체크 루프 시작")

        consecutive_healthy_checks = 0  # 연속 정상 체크 횟수
        reset_threshold = 6  # 6회 연속 정상 시 복구 시도 횟수 리셋 (3분)

        while self.running:
            try:
                await asyncio.sleep(self.health_check_interval)

                # 비정상 상태 장비 확인
                unhealthy_devices = []

                with self.task_lock:
                    for device_name, device_task in self.device_tasks.items():
                        if not device_task.is_healthy:
                            unhealthy_devices.append(device_name)

                        # 태스크가 종료된 경우 재시작
                        if device_task.task is not None and device_task.task.done():
                            self.logger.warning(f"⚠️ 장비 태스크 재시작: {device_name}")
                            device_task.task = asyncio.create_task(
                                self._device_polling_loop(device_task),
                                name=f"DevicePolling-{device_name}"
                            )

                # 건강성 리포트
                if unhealthy_devices:
                    self.logger.warning(f"⚠️ 비정상 상태 장비: {unhealthy_devices}")
                    consecutive_healthy_checks = 0  # 리셋
                else:
                    consecutive_healthy_checks += 1
                    self.logger.debug(f"✅ 모든 장비 정상 상태 (연속 {consecutive_healthy_checks}회)")

                # 연속 정상 시 장비별 오류 카운터 리셋
                if consecutive_healthy_checks >= reset_threshold:
                    with self.task_lock:
                        for device_task in self.device_tasks.values():
                            if device_task.consecutive_errors > 0:
                                self.logger.info(
                                    f"🔄 장비 복구 완료: {device_task.name} "
                                    f"(연속 오류 {device_task.consecutive_errors} → 0)"
                                )
                                device_task.consecutive_errors = 0
                                device_task.is_healthy = True
                    consecutive_healthy_checks = 0

            except asyncio.CancelledError:
                break
            except Exception as e:
                self.logger.error(f"❌ 건강성 체크 오류: {e}")

        self.logger.info("🛑 스케줄러 건강성 체크 루프 종료")
    
    def get_device_stats(self, device_name: str) -> Dict[str, Any]:
        """특정 장비의 통계 정보 반환"""
        with self.task_lock:
            if device_name in self.device_tasks:
                return self.device_tasks[device_name].get_stats()
        return {}
    
    def get_all_stats(self) -> Dict[str, Any]:
        """모든 장비의 통계 정보 반환"""
        with self.task_lock:
            device_stats = {}
            for device_name, device_task in self.device_tasks.items():
                device_stats[device_name] = device_task.get_stats()
            
            # 전체 통계
            total_executions = sum(dt.total_executions for dt in self.device_tasks.values())
            total_successful = sum(dt.successful_executions for dt in self.device_tasks.values())
            
            return {
                'scheduler_running': self.running,
                'start_time': self.start_time.isoformat() if self.start_time else None,
                'total_devices': len(self.device_tasks),
                'total_tasks_created': self.total_tasks_created,
                'total_tasks_failed': self.total_tasks_failed,
                'total_executions': total_executions,
                'total_successful': total_successful,
                'overall_success_rate': (total_successful / total_executions * 100) if total_executions > 0 else 0,
                'device_stats': device_stats
            }
    
    def log_status(self):
        """현재 스케줄러 상태 로깅"""
        stats = self.get_all_stats()
        
        self.logger.info("🔍 [스케줄러 상태 점검]")
        self.logger.info(f"   🔄 실행 상태: {'실행 중' if stats['scheduler_running'] else '정지'}")
        self.logger.info(f"   📊 관리 장비: {stats['total_devices']}개")
        self.logger.info(f"   📈 전체 성공률: {stats['overall_success_rate']:.1f}%")
        self.logger.info(f"   📋 총 실행: {stats['total_executions']}회")
        self.logger.info(f"   ✅ 성공: {stats['total_successful']}회")
        
        # 장비별 상태
        for device_name, device_stat in stats['device_stats'].items():
            status = "✅ 정상" if device_stat['is_healthy'] else "❌ 비정상"
            self.logger.info(f"   📱 {device_name}: {status} (성공률: {device_stat['success_rate']:.1f}%)")
            
            if device_stat['consecutive_errors'] > 0:
                self.logger.info(f"      ⚠️ 연속 오류: {device_stat['consecutive_errors']}회")
                self.logger.info(f"      ❌ 마지막 오류: {device_stat['last_error']}")
    
    def is_running(self) -> bool:
        """스케줄러 실행 상태 확인"""
        return self.running
    
    def get_device_count(self) -> int:
        """관리 중인 장비 수 반환"""
        return len(self.device_tasks)
    
    async def restart_device_task(self, device_name: str):
        """특정 장비 태스크 재시작"""
        with self.task_lock:
            if device_name in self.device_tasks:
                device_task = self.device_tasks[device_name]
                
                # 기존 태스크 취소
                if device_task.task and not device_task.task.done():
                    device_task.task.cancel()
                    try:
                        await device_task.task
                    except asyncio.CancelledError:
                        pass
                
                # 새 태스크 생성
                device_task.task = asyncio.create_task(
                    self._device_polling_loop(device_task),
                    name=f"DevicePolling-{device_name}"
                )
                
                # 상태 초기화
                device_task.consecutive_errors = 0
                device_task.is_healthy = True
                device_task.last_error = ""
                
                self.logger.info(f"🔄 장비 태스크 재시작 완료: {device_name}")
                return True
        
        return False 