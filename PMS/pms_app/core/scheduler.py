"""
스케줄러 모듈
장비 핸들러들의 주기적인 폴링 작업을 관리하는 스케줄러
"""

import asyncio
import logging
from typing import TYPE_CHECKING
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger

if TYPE_CHECKING:
    from ..devices.base import DeviceInterface


class PMSScheduler:
    """PMS 스케줄러 클래스"""
    
    def __init__(self):
        """스케줄러 초기화"""
        self.scheduler = AsyncIOScheduler()
        self.logger = logging.getLogger(self.__class__.__name__)
        self.running = False
    
    def add_polling_job(self, device_handler: 'DeviceInterface'):
        """
        장비 핸들러의 폴링 작업을 스케줄러에 추가
        
        Args:
            device_handler: 장비 핸들러 인스턴스
        """
        job_id = f"polling_{device_handler.name}"
        
        try:
            # 기존 작업이 있으면 제거
            if self.scheduler.get_job(job_id):
                self.scheduler.remove_job(job_id)
            
            # 새 작업 추가
            self.scheduler.add_job(
                func=self._safe_poll_and_publish,
                trigger=IntervalTrigger(seconds=device_handler.poll_interval),
                args=[device_handler],
                id=job_id,
                name=f"Polling {device_handler.name}",
                replace_existing=True,
                max_instances=1,  # 같은 작업이 동시에 1개만 실행되도록 제한
                coalesce=True,    # 누적된 작업들을 하나로 합치기
                misfire_grace_time=30  # 30초까지 지연 허용
            )
            
            self.logger.info(f"폴링 작업 등록됨: {device_handler.name} (주기: {device_handler.poll_interval}초)")
            
        except Exception as e:
            self.logger.error(f"폴링 작업 등록 실패 - {device_handler.name}: {e}")
            raise
    
    async def _safe_poll_and_publish(self, device_handler: 'DeviceInterface'):
        """
        안전한 폴링 및 발행 - 예외 처리 포함
        
        Args:
            device_handler: 장비 핸들러 인스턴스
        """
        try:
            await device_handler.poll_and_publish()
        except Exception as e:
            self.logger.error(f"장비 폴링 중 오류 발생 - {device_handler.name}: {e}")
    
    def start(self):
        """스케줄러 시작"""
        if not self.running:
            try:
                self.scheduler.start()
                self.running = True
                self.logger.info("스케줄러 시작됨")
            except Exception as e:
                self.logger.error(f"스케줄러 시작 실패: {e}")
                raise
        else:
            self.logger.warning("스케줄러가 이미 실행 중입니다")
    
    def shutdown(self, wait: bool = True):
        """
        스케줄러 종료
        
        Args:
            wait: 실행 중인 작업이 완료될 때까지 대기할지 여부
        """
        if self.running:
            try:
                self.scheduler.shutdown(wait=wait)
                self.running = False
                self.logger.info("스케줄러 종료됨")
            except Exception as e:
                self.logger.error(f"스케줄러 종료 중 오류: {e}")
        else:
            self.logger.info("스케줄러가 이미 중지된 상태입니다")
    
    def pause_job(self, job_id: str):
        """
        특정 작업 일시 정지
        
        Args:
            job_id: 작업 ID
        """
        try:
            self.scheduler.pause_job(job_id)
            self.logger.info(f"작업 일시 정지됨: {job_id}")
        except Exception as e:
            self.logger.error(f"작업 일시 정지 실패 - {job_id}: {e}")
    
    def resume_job(self, job_id: str):
        """
        특정 작업 재개
        
        Args:
            job_id: 작업 ID
        """
        try:
            self.scheduler.resume_job(job_id)
            self.logger.info(f"작업 재개됨: {job_id}")
        except Exception as e:
            self.logger.error(f"작업 재개 실패 - {job_id}: {e}")
    
    def remove_job(self, job_id: str):
        """
        특정 작업 제거
        
        Args:
            job_id: 작업 ID
        """
        try:
            self.scheduler.remove_job(job_id)
            self.logger.info(f"작업 제거됨: {job_id}")
        except Exception as e:
            self.logger.error(f"작업 제거 실패 - {job_id}: {e}")
    
    def get_jobs(self):
        """등록된 모든 작업 조회"""
        return self.scheduler.get_jobs()
    
    def is_running(self) -> bool:
        """스케줄러 실행 상태 확인"""
        return self.running 