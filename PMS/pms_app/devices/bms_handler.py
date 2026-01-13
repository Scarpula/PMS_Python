"""
BMS (Battery Management System) 핸들러
범한배터리 BMS 장비에 특화된 데이터 읽기 및 처리 로직
Function Code 0x03: Read Holding Register
Function Code 0x06: Write Single Register

Context7 패턴 적용:
- Taskiq 스타일 Queue Worker 개선
- AsyncPG 스타일 Connection Pool 관리
- 배치 처리 및 상태 관리 강화
"""

import asyncio
from typing import Dict, Any, Optional, List
from pymodbus.client.tcp import AsyncModbusTcpClient
from pymodbus.exceptions import ModbusException
from datetime import datetime, timedelta
import time

from .base import DeviceInterface


class ModbusConnectionPool:
    """Modbus 연결 풀 - AsyncPG Connection Pool 패턴 적용"""
    
    def __init__(self, host: str, port: int = 502, max_connections: int = 3, timeout: float = 3.0):
        self.host = host
        self.port = port
        self.max_connections = max_connections
        self.timeout = timeout
        self._pool = asyncio.Queue(maxsize=max_connections)
        self._connections = set()
        self._created_connections = 0
        self._pool_initialized = False
        
    async def initialize(self):
        """연결 풀 초기화 - Taskiq startup 패턴"""
        if self._pool_initialized:
            return
            
        # 최소 1개 연결 미리 생성
        try:
            client = await self._create_connection()
            if client:
                await self._pool.put(client)
                self._pool_initialized = True
        except Exception:
            pass  # 초기화 실패해도 런타임에 생성 시도
    
    async def _create_connection(self) -> Optional[AsyncModbusTcpClient]:
        """새 연결 생성 - 중복 연결 방지"""
        if self._created_connections >= self.max_connections:
            return None
            
        try:
            client = AsyncModbusTcpClient(
                host=self.host,
                port=self.port,
                timeout=self.timeout
            )
            
            # 연결 시도 전 지연 (중복 연결 방지)
            await asyncio.sleep(0.1)
            
            # 연결 시도
            success = await asyncio.wait_for(client.connect(), timeout=self.timeout)
            if success and client.connected:
                self._connections.add(client)
                self._created_connections += 1
                return client
            else:
                client.close()
                return None
        except Exception as e:
            # 연결 실패 시 더 구체적인 로그
            if "원격 컴퓨터가 네트워크 연결을 거부했습니다" in str(e):
                # 이미 연결이 있는 경우 로그 레벨 낮춤
                pass
            return None
    
    async def acquire(self) -> Optional[AsyncModbusTcpClient]:
        """연결 획득 - AsyncPG acquire 패턴"""
        # 풀에서 사용 가능한 연결 확인
        try:
            client = self._pool.get_nowait()
            if client and client.connected:
                return client
            elif client:
                # 끊어진 연결은 정리
                self._cleanup_connection(client)
        except asyncio.QueueEmpty:
            pass
            
        # 새 연결 생성 시도
        client = await self._create_connection()
        return client
    
    async def release(self, client: AsyncModbusTcpClient):
        """연결 반환 - AsyncPG release 패턴"""
        if not client:
            return
            
        if client.connected and self._pool.qsize() < self.max_connections:
            try:
                self._pool.put_nowait(client)
            except asyncio.QueueFull:
                self._cleanup_connection(client)
        else:
            self._cleanup_connection(client)
    
    def _cleanup_connection(self, client: AsyncModbusTcpClient):
        """연결 정리"""
        try:
            if client in self._connections:
                self._connections.remove(client)
                self._created_connections -= 1
            if client.connected:
                client.close()
        except Exception:
            pass
    
    async def close_all(self):
        """모든 연결 종료"""
        # 풀의 모든 연결 정리
        while not self._pool.empty():
            try:
                client = self._pool.get_nowait()
                self._cleanup_connection(client)
            except asyncio.QueueEmpty:
                break
        
        # 남은 연결들 정리
        for client in list(self._connections):
            self._cleanup_connection(client)
        
        self._pool_initialized = False


class DeviceState:
    """장비 상태 관리 - Taskiq State 패턴 적용"""
    
    def __init__(self):
        self.connection_pool: Optional[ModbusConnectionPool] = None
        self.last_successful_read: Optional[datetime] = None
        self.last_successful_write: Optional[datetime] = None
        self.consecutive_errors = 0
        self.total_requests = 0
        self.successful_requests = 0
        self.is_healthy = True
        self.health_check_interval = 30  # 30초
        self.last_health_check: Optional[datetime] = None
        
    def update_read_success(self):
        """읽기 성공 시 상태 업데이트"""
        self.last_successful_read = datetime.now()
        self.consecutive_errors = 0
        self.successful_requests += 1
        self.total_requests += 1
        self.is_healthy = True
    
    def update_write_success(self):
        """쓰기 성공 시 상태 업데이트"""
        self.last_successful_write = datetime.now()
        self.consecutive_errors = 0
        self.successful_requests += 1
        self.total_requests += 1
        self.is_healthy = True
    
    def update_failure(self):
        """실패 시 상태 업데이트"""
        self.consecutive_errors += 1
        self.total_requests += 1
        
        # 연속 5회 실패 시 비정상 상태
        if self.consecutive_errors >= 5:
            self.is_healthy = False
    
    def get_success_rate(self) -> float:
        """성공률 계산"""
        if self.total_requests == 0:
            return 0.0
        return (self.successful_requests / self.total_requests) * 100
    
    def needs_health_check(self) -> bool:
        """건강 상태 체크 필요 여부"""
        if not self.last_health_check:
            return True
        return datetime.now() - self.last_health_check > timedelta(seconds=self.health_check_interval)


class BMSHandler(DeviceInterface):
    """BMS 핸들러 클래스 - 범한배터리 BMS 전용"""
    
    def __init__(self, device_config: Dict[str, Any], mqtt_client, system_config: Dict[str, Any]):
        """BMS 핸들러 초기화"""
        super().__init__(device_config, mqtt_client, system_config)
        
        # Connection Pool 초기화 - 중복 연결 방지를 위해 최대 1개 연결
        self._connection_pool = ModbusConnectionPool(
            host=self.ip,
            port=self.port,
            max_connections=1,
            timeout=3.0
        )
        
        # 장비 상태 관리
        self._device_state = DeviceState()
        self._device_state.connection_pool = self._connection_pool
        
        # Request Queue 시스템 - Taskiq 패턴 개선
        self._request_queue = asyncio.Queue(maxsize=100)  # 최대 100개 요청 큐
        self._queue_worker_running = False
        self._queue_worker_task = None
        
        # 배치 처리 설정
        self._batch_size = 10
        self._batch_timeout = 0.1  # 100ms
        
        # 성능 모니터링
        self._performance_stats = {
            'total_requests': 0,
            'successful_requests': 0,
            'failed_requests': 0,
            'average_response_time': 0.0,
            'last_batch_size': 0
        }
        
        # Queue Worker는 첫 연결 시에 시작
    
    async def _initialize_connections(self):
        """연결 초기화 - Taskiq startup 이벤트 패턴"""
        try:
            await self._connection_pool.initialize()
            self.logger.info(f"🏊 BMS Connection Pool 초기화 완료: {self.ip}")
            return True
        except Exception as e:
            self.logger.error(f"❌ BMS Connection Pool 초기화 실패: {e}")
            return False
    
    def _start_queue_worker(self):
        """Request Queue Worker 시작 - Taskiq Worker 패턴 강화"""
        # 기존 worker가 정상 실행 중인지 확인
        if self._queue_worker_running and self._queue_worker_task and not self._queue_worker_task.done():
            self.logger.debug(f"🔄 BMS Queue Worker 이미 실행 중: {self.ip}")
            return
            
        # 기존 task가 완료되었거나 오류 상태인 경우 재시작
        self._queue_worker_running = False
        if self._queue_worker_task and not self._queue_worker_task.done():
            try:
                self._queue_worker_task.cancel()
            except:
                pass
                
        try:
            loop = asyncio.get_running_loop()
            self._queue_worker_task = loop.create_task(self._queue_worker())
            self._queue_worker_running = True
            self.logger.info(f"🚀 BMS Request Queue Worker 시작/재시작: {self.ip}")
        except RuntimeError:
                # 이벤트 루프가 실행되지 않은 경우
                self.logger.warning(f"⏰ BMS Queue Worker 시작 실패 - 이벤트 루프 없음: {self.ip}")
                try:
                    self._queue_worker_task = asyncio.create_task(self._queue_worker())
                    self._queue_worker_running = True
                    self.logger.info(f"🚀 BMS Request Queue Worker 시작 (create_task): {self.ip}")
                except Exception as e:
                    self.logger.error(f"❌ BMS Queue Worker 시작 실패: {e}")
    
    def _ensure_queue_worker_running(self):
        """📝 Queue Worker 상태 감시 및 자동 재시작"""
        try:
            # Queue Worker 상태 확인
            if (not self._queue_worker_running or 
                not self._queue_worker_task or 
                self._queue_worker_task.done()):
                
                # 작업이 완료되었거나 오류 상태인 경우 재시작
                if self._queue_worker_task and self._queue_worker_task.done():
                    try:
                        # 작업 결과 확인 (예외가 있었는지)
                        exception = self._queue_worker_task.exception()
                        if exception:
                            self.logger.warning(f"⚠️ BMS Queue Worker 예외로 종료됨: {exception}")
                        else:
                            self.logger.info(f"ℹ️ BMS Queue Worker 정상 종료됨")
                    except:
                        pass
                
                self.logger.warning(f"🔄 BMS Queue Worker 중단됨 - 재시작 시도: {self.ip}")
                self._start_queue_worker()
                
        except Exception as e:
            self.logger.error(f"❌ BMS Queue Worker 상태 확인 중 오류: {e}")
    
    async def _queue_worker(self):
        """Request Queue 처리 워커 - Taskiq + 배치 처리 패턴"""
        self.logger.info(f"🔄 BMS Queue Worker 실행 시작 (배치 처리 지원): {self.ip}")
        consecutive_errors = 0
        max_consecutive_errors = 5
        
        while self._queue_worker_running:
            try:
                # 배치 요청 수집 - Taskiq 배치 처리 패턴
                batch_requests = await self._collect_batch_requests()
                
                if batch_requests:
                    await self._process_batch_requests(batch_requests)
                    consecutive_errors = 0  # 성공 시 오류 카운트 리셋
                else:
                    # 빈 배치는 정상 상황
                    consecutive_errors = 0
                    await asyncio.sleep(0.05)  # 짧은 대기
                    continue
                
            except Exception as e:
                consecutive_errors += 1
                error_msg = str(e)
                
                if "invalid state" in error_msg.lower():
                    self.logger.error(f"❌ BMS Queue Worker invalid state 오류: {e}")
                    await self._handle_connection_error()
                else:
                    self.logger.error(f"❌ BMS Queue Worker 배치 처리 오류 #{consecutive_errors}: {e}")
                
                # 연속 오류가 많을 때 복구 시도
                if consecutive_errors >= max_consecutive_errors:
                    self.logger.warning(f"⚠️ BMS 연속 오류 {consecutive_errors}회, 복구 시도")
                    await self._handle_connection_error()
                    await asyncio.sleep(2.0)
                    consecutive_errors = 0
                
                await asyncio.sleep(0.5 + (consecutive_errors * 0.5))
        
        self.logger.info(f"🛑 BMS Queue Worker 종료: {self.ip}")
        self._queue_worker_running = False
    
    async def _collect_batch_requests(self) -> List[Dict[str, Any]]:
        """배치 요청 수집 - Taskiq 배치 패턴"""
        requests = []
        deadline = time.time() + self._batch_timeout
        
        while len(requests) < self._batch_size and time.time() < deadline:
            try:
                # 남은 시간 계산
                remaining_time = max(0.01, deadline - time.time())
                request = await asyncio.wait_for(
                    self._request_queue.get(), 
                    timeout=remaining_time
                )
                requests.append(request)
            except asyncio.TimeoutError:
                break
        
        return requests
    
    async def _process_batch_requests(self, requests: List[Dict[str, Any]]):
        """배치 요청 처리"""
        start_time = time.time()
        successful_count = 0
        
        # Connection Pool에서 연결 획득
        client = await self._connection_pool.acquire()
        
        try:
            if not client:
                # 연결 실패 시 모든 요청 실패 처리
                for request in requests:
                    self._handle_failed_request(request, "연결 획득 실패")
                return
            
            # 배치 내 요청들을 순차 처리
            for request in requests:
                try:
                    success = await self._execute_single_request(client, request)
                    if success:
                        successful_count += 1
                        self._device_state.update_read_success() if request.get('type') == 'read' else self._device_state.update_write_success()
                    else:
                        self._device_state.update_failure()
                        
                    # 요청 간 짧은 대기
                    await asyncio.sleep(0.02)
                    
                except Exception as e:
                    self.logger.debug(f"배치 내 요청 처리 오류: {e}")
                    self._handle_failed_request(request, str(e))
                    self._device_state.update_failure()
                
                # 큐 작업 완료 표시
                try:
                    self._request_queue.task_done()
                except:
                    pass
                    
        finally:
            # 연결 반환
            if client:
                await self._connection_pool.release(client)
        
        # 성능 통계 업데이트
        processing_time = time.time() - start_time
        self._update_performance_stats(len(requests), successful_count, processing_time)
        
        if requests:
            self.logger.debug(f"📦 배치 처리 완료: {successful_count}/{len(requests)} 성공, {processing_time:.3f}초")
    
    async def _execute_single_request(self, client: AsyncModbusTcpClient, request: Dict[str, Any]) -> bool:
        """단일 요청 실행"""
        request_type = request.get('type')
        
        try:
            if request_type == 'read':
                return await self._execute_read_request_with_client(client, request)
            elif request_type == 'write':
                return await self._execute_write_request_with_client(client, request)
            else:
                self.logger.warning(f"⚠️ 알 수 없는 요청 타입: {request_type}")
                self._handle_failed_request(request, "알 수 없는 요청 타입")
                return False
        except Exception as e:
            self._handle_failed_request(request, str(e))
            return False
    
    def _handle_failed_request(self, request: Dict[str, Any], error_msg: str):
        """실패한 요청 처리"""
        if 'future' in request and not request['future'].done():
            try:
                request['future'].set_result(None)
            except:
                pass
        
        self.logger.debug(f"요청 실패: {error_msg}")
    
    def _update_performance_stats(self, total_requests: int, successful_requests: int, processing_time: float):
        """성능 통계 업데이트"""
        self._performance_stats['total_requests'] += total_requests
        self._performance_stats['successful_requests'] += successful_requests
        self._performance_stats['failed_requests'] += (total_requests - successful_requests)
        
        # 평균 응답 시간 계산 (지수 평활법)
        if total_requests > 0:
            avg_time_per_request = processing_time / total_requests
            if self._performance_stats['average_response_time'] == 0:
                self._performance_stats['average_response_time'] = avg_time_per_request
            else:
                alpha = 0.1  # 평활 계수
                self._performance_stats['average_response_time'] = (
                    alpha * avg_time_per_request + 
                    (1 - alpha) * self._performance_stats['average_response_time']
                )
        
        self._performance_stats['last_batch_size'] = total_requests
    
    async def _handle_connection_error(self):
        """연결 오류 처리 - AsyncPG 스타일 복구"""
        try:
            self.logger.info(f"🔄 BMS 연결 오류 복구 시작: {self.ip}")
            
            # 1. 기존 연결 정리
            self.connected = False
            await self._connection_pool.close_all()
            
            # 2. 잠시 대기
            await asyncio.sleep(1.0)
            
            # 3. 연결 풀 재초기화
            await self._connection_pool.initialize()
            
            # 4. 상태 업데이트
            if self._connection_pool._pool_initialized:
                self.connected = True
                self.logger.info(f"✅ BMS 연결 복구 성공: {self.ip}")
            else:
                self.logger.warning(f"❌ BMS 연결 복구 실패: {self.ip}")
                
        except Exception as e:
            self.logger.error(f"❌ BMS 연결 복구 중 오류: {e}")
    
    async def _execute_read_request_with_client(self, client: AsyncModbusTcpClient, request: Dict[str, Any]) -> bool:
        """클라이언트를 사용한 READ 요청 실행"""
        address = request.get('address', 0)
        count = request.get('count', 1)
        slave_id = request.get('slave_id', self.slave_id)
        future = request['future']
        
        try:
            if not client or not client.connected:
                future.set_result(None)
                return False
            
            response = await asyncio.wait_for(
                client.read_holding_registers(
                address=address, count=count, slave=slave_id
                ),
                timeout=3.0
            )
            
            if response.isError():
                future.set_result(None)
                return False
            else:
                future.set_result(response)
                return True
                
        except asyncio.TimeoutError:
            self.logger.warning(f"❌ BMS READ 타임아웃 (주소={address})")
            future.set_result(None)
            return False
        except Exception as e:
            self.logger.debug(f"BMS READ 오류 (주소={address}): {e}")
            future.set_result(None)
            return False
    
    async def _execute_write_request_with_client(self, client: AsyncModbusTcpClient, request: Dict[str, Any]) -> bool:
        """클라이언트를 사용한 WRITE 요청 실행"""
        address = request['address']
        value = request['value']
        slave_id = request.get('slave_id', self.slave_id)
        future = request['future']
        
        try:
            if not client or not client.connected:
                future.set_result(False)
                return False
            
            response = await asyncio.wait_for(
                client.write_register(address=address, value=value, slave=slave_id),
                timeout=3.0
            )
            
            if response.isError():
                self.logger.error(f"❌ BMS WRITE 오류: {response}")
                future.set_result(False)
                return False
            else:
                self.logger.info(f"✅ BMS WRITE 성공: 주소={address}, 값={value}")
                future.set_result(True)
                return True
                
        except asyncio.TimeoutError:
            self.logger.warning(f"❌ BMS WRITE 타임아웃 (주소={address})")
            future.set_result(False)
            return False
        except Exception as e:
            self.logger.error(f"❌ BMS WRITE 오류: {e}")
            future.set_result(False)
            return False
    
    async def _queue_read_register(self, address: int, count: int = 1):
        """Request Queue를 통한 READ 요청"""
        # Future 객체 생성
        future = asyncio.Future()
        
        # Request 생성
        request = {
            'type': 'read',
            'address': address,
            'count': count,
            'slave_id': self.slave_id,
            'future': future
        }
        
        # 큐에 요청 추가
        await self._request_queue.put(request)
        
        # 결과 대기 (최대 5초)
        try:
            result = await asyncio.wait_for(future, timeout=5.0)
            return result
        except asyncio.TimeoutError:
            self.logger.error(f"❌ BMS READ 타임아웃: 주소={address}")
            return None
    
    async def _queue_write_register(self, address: int, value: int) -> bool:
        """Request Queue를 통한 WRITE 요청"""
        # Future 객체 생성
        future = asyncio.Future()
        
        # Request 생성
        request = {
            'type': 'write',
            'address': address,
            'value': value,
            'slave_id': self.slave_id,
            'future': future
        }
        
        # 큐에 요청 추가
        await self._request_queue.put(request)
        
        # 결과 대기 (최대 5초)
        try:
            result = await asyncio.wait_for(future, timeout=5.0)
            return result
        except asyncio.TimeoutError:
            self.logger.error(f"❌ BMS WRITE 타임아웃: 주소={address}, 값={value}")
            return False
    
    async def _connect_modbus(self) -> bool:
        """Modbus TCP 연결 - Connection Pool 사용"""
        async with self._get_connection_lock():
            try:
                # Connection Pool 초기화
                if not self._connection_pool._pool_initialized:
                    success = await self._initialize_connections()
                    if not success:
                        return False
                
                # 연결 테스트
                client = await self._connection_pool.acquire()
                if client:
                    await self._connection_pool.release(client)
                    self.connected = True
                    
                    # 첫 연결 성공 시 Queue Worker 시작
                    if not self._queue_worker_running:
                        self._start_queue_worker()
                        
                    self.logger.debug(f"✅ BMS Modbus 연결 성공: {self.ip}:{self.port}")
                    return True
                else:
                    self.connected = False
                    self.logger.warning(f"❌ BMS Modbus 연결 실패: {self.ip}:{self.port}")
                    return False
                
            except Exception as e:
                self.logger.error(f"❌ BMS Modbus 연결 중 오류: {e}")
                self.connected = False
                return False
    
    async def _disconnect_modbus(self):
        """Modbus TCP 연결 해제"""
        try:
            self.connected = False
            await self._connection_pool.close_all()
            self.logger.debug("BMS Modbus 연결 해제됨")
        except Exception as e:
            self.logger.warning(f"BMS Modbus 연결 해제 중 오류: {e}")
            self.connected = False
    
    def get_performance_stats(self) -> Dict[str, Any]:
        """성능 통계 반환"""
        success_rate = 0.0
        if self._performance_stats['total_requests'] > 0:
            success_rate = (self._performance_stats['successful_requests'] / 
                          self._performance_stats['total_requests']) * 100
        
        return {
            'total_requests': self._performance_stats['total_requests'],
            'successful_requests': self._performance_stats['successful_requests'],
            'failed_requests': self._performance_stats['failed_requests'],
            'success_rate': round(success_rate, 2),
            'average_response_time': round(self._performance_stats['average_response_time'], 4),
            'last_batch_size': self._performance_stats['last_batch_size'],
            'device_health': self._device_state.is_healthy,
            'consecutive_errors': self._device_state.consecutive_errors,
            'last_successful_read': self._device_state.last_successful_read,
            'last_successful_write': self._device_state.last_successful_write
        }
    
    async def health_check(self) -> Dict[str, Any]:
        """장비 건강 상태 체크 - Taskiq Health Check 패턴"""
        if not self._device_state.needs_health_check():
            return {'status': 'healthy', 'last_check': self._device_state.last_health_check}
        
        # 📝 Queue Worker 상태 확인 및 자동 재시작
        self._ensure_queue_worker_running()
        
        health_status = {
            'timestamp': datetime.now(),
            'connection_pool_healthy': self._connection_pool._pool_initialized,
            'queue_worker_running': self._queue_worker_running,
            'queue_worker_task_alive': self._queue_worker_task and not self._queue_worker_task.done() if self._queue_worker_task else False,
            'device_healthy': self._device_state.is_healthy,
            'performance': self.get_performance_stats(),
            'queue_size': self._request_queue.qsize()
        }
        
        # 간단한 연결 테스트
        try:
            client = await self._connection_pool.acquire()
            if client:
                await self._connection_pool.release(client)
                health_status['connection_test'] = 'success'
            else:
                health_status['connection_test'] = 'failed'
        except Exception as e:
            health_status['connection_test'] = f'error: {e}'
        
        self._device_state.last_health_check = datetime.now()
        
        return health_status

    def _group_consecutive_registers(self, section_data: Dict[str, Any]) -> List[Dict[str, Any]]:
        """연속된 레지스터들을 청크로 그룹화 (최대 120 Words)"""
        # Function Code가 0x03인 읽기 가능한 레지스터만 필터링
        readable_registers = {}
        for key, register_info in section_data.items():
            if register_info.get('function_code') == '0x03':
                readable_registers[key] = register_info
        
        if not readable_registers:
            return []
        
        # 주소별로 정렬
        sorted_registers = sorted(
            readable_registers.items(), 
            key=lambda x: x[1]['address']
        )
        
        chunks = []
        current_chunk = []
        current_start_addr = None
        current_end_addr = None
        max_chunk_size = 120  # 최대 120 Words
        
        for key, register_info in sorted_registers:
            address = register_info['address']
            register_count = register_info.get('registers', 1)
            
            # 첫 번째 레지스터이거나 연속되지 않은 경우
            if (current_start_addr is None or 
                address != current_end_addr or 
                (current_start_addr is not None and (address - current_start_addr + register_count) > max_chunk_size)):
                
                # 현재 청크를 저장하고 새 청크 시작
                if current_chunk and current_start_addr is not None and current_end_addr is not None:
                    chunks.append({
                        'start_address': current_start_addr,
                        'count': current_end_addr - current_start_addr,
                        'registers': current_chunk
                    })
                
                current_chunk = [(key, register_info)]
                current_start_addr = address
                current_end_addr = address + register_count
            else:
                # 연속된 레지스터인 경우 현재 청크에 추가
                current_chunk.append((key, register_info))
                current_end_addr = address + register_count
        
        # 마지막 청크 추가
        if current_chunk and current_start_addr is not None and current_end_addr is not None:
            chunks.append({
                'start_address': current_start_addr,
                'count': current_end_addr - current_start_addr,
                'registers': current_chunk
            })
        
        return chunks

    async def read_data(self) -> Optional[Dict[str, Any]]:
        """
        BMS 장비에서 데이터를 읽어옵니다.
        🚀 병렬 청크 읽기로 최적화된 데이터 읽기
        Function Code 0x03 (Read Holding Register) 사용
        
        Returns:
            읽어온 원시 데이터 딕셔너리 또는 None (실패 시)
        """
        if not await self._ensure_connection():
            return None

        async with self._get_connection_lock():
            try:
                if not self._connection_pool._pool_initialized:
                    self.logger.warning("데이터 읽기 시도 전 연결 풀이 초기화되지 않았습니다.")
                    return None
            
                raw_data = {}
                
                # 각 섹션별로 청크 수집
                sections = [
                    'data_registers',
                    'module_voltages', 
                    'status_registers',
                    'module_status_registers',
                    'module_temperatures',
                    'cell_voltages',
                    'optional_metering_registers'
                ]
                
                # 🚀 모든 청크를 수집하여 병렬 처리 준비
                all_chunks = []
                for section in sections:
                    section_data = self.device_map.get(section, {})
                    chunks = self._group_consecutive_registers(section_data)
                    all_chunks.extend(chunks)
                
                if not all_chunks:
                    self.logger.warning("읽을 데이터 청크가 없습니다")
                    return None
                
                # 🚀 청크별 병렬 처리
                chunk_results = await self._process_chunks_parallel(all_chunks)
                
                # 결과 병합
                total_chunks = len(all_chunks)
                successful_chunks = sum(1 for result in chunk_results if result is not None)
                
                for chunk_data in chunk_results:
                    if chunk_data:
                        raw_data.update(chunk_data)
                
                if raw_data:
                    efficiency = (successful_chunks / total_chunks * 100) if total_chunks > 0 else 0
                    self.logger.debug(f"BMS 병렬 읽기 완료: {len(raw_data)}개 레지스터, {successful_chunks}/{total_chunks} 청크 성공 ({efficiency:.1f}%)")
                    return raw_data
                else:
                    self.logger.warning("BMS에서 읽어온 데이터가 없습니다")
                    return None
                
            except ModbusException as e:
                self.logger.error(f"BMS Modbus 예외 발생: {e}")
                await self._disconnect_modbus()
                return None
            except Exception as e:
                self.logger.error(f"BMS 데이터 읽기 중 예외 발생: {e}")
                return None
    
    async def _process_chunks_parallel(self, chunks: List[Dict[str, Any]]) -> List[Optional[Dict[str, Any]]]:
        """
        🚀 청크들을 병렬로 처리 (중복 연결 방지)
        
        Args:
            chunks: 처리할 청크 목록
            
        Returns:
            청크별 결과 데이터 목록
        """
        # 🔧 중복 연결 방지를 위해 동시 실행 태스크 수를 1로 제한
        max_concurrent_tasks = 1
        
        self.logger.debug(f"📊 청크 처리 시작: {len(chunks)}개 청크, 동시 실행: {max_concurrent_tasks}개")
        
        # 세마포어를 사용하여 동시 실행 제한
        semaphore = asyncio.Semaphore(max_concurrent_tasks)
        
        # 각 청크에 대해 태스크 생성
        tasks = []
        for i, chunk in enumerate(chunks):
            task = asyncio.create_task(
                self._process_single_chunk_with_semaphore(semaphore, chunk, i),
                name=f"BMS-Chunk-{i}"
            )
            tasks.append(task)
        
        # 모든 태스크를 병렬로 실행하고 결과 수집
        try:
            results = await asyncio.gather(*tasks, return_exceptions=True)
            
            # 예외 처리
            chunk_results = []
            for i, result in enumerate(results):
                if isinstance(result, Exception):
                    self.logger.debug(f"청크 {i} 처리 중 예외 발생: {result}")
                    chunk_results.append(None)
                else:
                    chunk_results.append(result)
            
            return chunk_results
            
        except Exception as e:
            self.logger.error(f"병렬 청크 처리 중 오류: {e}")
            return [None] * len(chunks)
    
    async def _process_single_chunk_with_semaphore(self, semaphore: asyncio.Semaphore, chunk: Dict[str, Any], chunk_index: int) -> Optional[Dict[str, Any]]:
        """
        🚀 세마포어를 사용한 단일 청크 처리
        
        Args:
            semaphore: 동시 실행 제한용 세마포어
            chunk: 처리할 청크
            chunk_index: 청크 인덱스 (로깅용)
            
        Returns:
            청크 처리 결과
        """
        async with semaphore:
            return await self._process_single_chunk(chunk, chunk_index)
    
    async def _process_single_chunk(self, chunk: Dict[str, Any], chunk_index: int) -> Optional[Dict[str, Any]]:
        """
        🚀 단일 청크 처리 (독립적인 연결 사용)
        
        Args:
            chunk: 처리할 청크
            chunk_index: 청크 인덱스 (로깅용)
            
        Returns:
            청크 처리 결과
        """
        client = None
        try:
            # 연결 풀에서 독립적인 연결 획득
            client = await self._connection_pool.acquire()
            if not client:
                self.logger.debug(f"청크 {chunk_index} - 연결 획득 실패")
                return None
            
            # 청크 데이터 읽기
            start_time = time.time()
            response = await asyncio.wait_for(
                client.read_holding_registers(
                    address=chunk['start_address'],
                    count=chunk['count'],
                    slave=self.slave_id
                ),
                timeout=3.0
            )
            read_time = time.time() - start_time
            
            if response is None or response.isError():
                self.logger.debug(f"청크 {chunk_index} 읽기 실패 - 주소:{chunk['start_address']}, 크기:{chunk['count']}")
                return None
            
            # 청크 내 각 레지스터 값 추출
            chunk_data = {}
            for key, register_info in chunk['registers']:
                try:
                    address = register_info['address']
                    data_type = register_info.get('data_type', 'uint16')
                    register_count = register_info.get('registers', 1)
                    
                    # 청크 내에서의 오프셋 계산
                    offset = address - chunk['start_address']
                    
                    # 데이터 타입에 따른 값 변환
                    if register_count == 1:
                        if offset < len(response.registers):
                            raw_value = response.registers[offset]
                            if data_type == 'int16' and raw_value > 32767:
                                raw_value = raw_value - 65536
                        else:
                            continue
                    else:
                        # 32비트 데이터 (2개 레지스터)
                        if offset + 1 < len(response.registers):
                            raw_value = (response.registers[offset] << 16) + response.registers[offset + 1]
                            if data_type == 'int32' and raw_value > 2147483647:
                                raw_value = raw_value - 4294967296
                        else:
                            continue
                    
                    chunk_data[key] = raw_value
                    
                    # Error Code 읽기 성공 시 간단 로그
                    if 'error_code' in key and raw_value != 0:
                        active_bits = []
                        for i in range(16):
                            if raw_value & (1 << i):
                                active_bits.append(f"Bit {i}")
                        self.logger.warning(f"⚠️ {key}: {raw_value} (0x{raw_value:04X}) - 활성 비트: {active_bits}")
                    
                except Exception as e:
                    self.logger.debug(f"레지스터 값 추출 오류 - {key}: {e}")
                    continue
            
            self.logger.debug(f"청크 {chunk_index} 성공 - {len(chunk_data)}개 레지스터, {read_time:.3f}초")
            return chunk_data
            
        except asyncio.TimeoutError:
            self.logger.debug(f"청크 {chunk_index} 타임아웃 - 주소:{chunk['start_address']}")
            return None
        except Exception as e:
            self.logger.debug(f"청크 {chunk_index} 처리 오류: {e}")
            return None
        finally:
            # 연결 반환
            if client:
                await self._connection_pool.release(client)
    
    async def write_register(self, register_name: str, value: int) -> bool:
        """
        BMS 제어 레지스터에 값을 씁니다.
        독립적인 Write 전용 클라이언트를 사용하여 폴링과 완전히 분리합니다.
        Function Code 0x06 (Write Single Register) 사용
        
        Args:
            register_name: 레지스터 이름
            value: 쓸 값
            
        Returns:
            성공 여부
        """
        self.logger.info(f"🔥 BMS write_register 시작: {register_name} = {value}")
        
        # 📝 Queue Worker 상태 확인 및 자동 재시작
        self._ensure_queue_worker_running()
        
        # 레지스터 정보 확인
        control_registers = self.device_map.get('control_registers', {})
        
        if register_name not in control_registers:
            self.logger.error(f"❌ 알 수 없는 BMS 제어 레지스터: {register_name}")
            return False
        
        register_info = control_registers[register_name]
        
        # Function Code가 0x06 (Write)인지 확인
        if register_info.get('function_code') != '0x06':
            self.logger.error(f"❌ 읽기 전용 레지스터: {register_name}")
            return False
        
        address = register_info['address']
        
        # Request Queue를 사용하여 순차 WRITE 처리
        return await self._queue_write_register(address, value)
    
    async def process_data(self, raw_data: Dict[str, Any]) -> Dict[str, Any]:
        """
        BMS 원시 데이터를 가공합니다.
        
        Args:
            raw_data: 원시 데이터 딕셔너리
            
        Returns:
            가공된 데이터 딕셔너리
        """
        processed_data = {}
        
        # 모든 섹션에서 레지스터 정보 가져오기
        all_registers = {}
        sections = [
            'data_registers',
            'module_voltages', 
            'status_registers',
            'module_status_registers',
            'module_temperatures',
            'cell_voltages',
            'optional_metering_registers'
        ]
        
        for section in sections:
            all_registers.update(self.device_map.get(section, {}))
        
        try:
            for key, raw_value in raw_data.items():
                if key in all_registers:
                    register_info = all_registers[key]
                    scale = register_info.get('scale', 1)
                    unit = register_info.get('unit', '')
                    description = register_info.get('description', key)
                    register_type = register_info.get('type', '')
                    
                    # 스케일 적용
                    processed_value = raw_value * scale
                    
                    # 비트마스크 타입 처리
                    if register_type == 'bitmask':
                        processed_data[key] = self._process_bitmask(raw_value, register_info, description)
                    else:
                        processed_data[key] = {
                            'value': processed_value,
                            'unit': unit,
                            'description': description,
                            'raw_value': raw_value,
                            'type': register_type
                        }
                else:
                    # 맵에 없는 데이터는 원시값 그대로
                    processed_data[key] = {
                        'value': raw_value,
                        'unit': '',
                        'description': key,
                        'raw_value': raw_value,
                        'type': 'unknown'
                    }
            
            # BMS 특화 계산
            self._calculate_derived_values(processed_data)
            
            self.logger.debug(f"BMS 데이터 가공 완료: {len(processed_data)}개 항목")
            return processed_data
            
        except Exception as e:
            self.logger.error(f"BMS 데이터 가공 중 오류: {e}")
            return {}
    
    def _process_bitmask(self, raw_value: int, register_info: Dict[str, Any], description: str) -> Dict[str, Any]:
        """
        비트마스크 데이터를 처리합니다.
        
        Args:
            raw_value: 원시 비트마스크 값
            register_info: 레지스터 정보
            description: 레지스터 설명
            
        Returns:
            처리된 비트마스크 데이터
        """
        bit_definitions = register_info.get('bit_definitions', {})
        active_bits = []
        bit_status = {}
        status_values = {}
        
        for bit_pos, bit_desc in bit_definitions.items():
            bit_num = int(bit_pos)
            is_set = bool(raw_value & (1 << bit_num))
            bit_status[f"bit_{bit_num:02d}"] = {
                'active': is_set,
                'description': bit_desc
            }
            
            # 비트 값에 따른 상태 해석
            status_value = self._interpret_bit_status(bit_num, is_set, bit_desc, raw_value)
            if status_value:
                status_values[f"bit_{bit_num:02d}_status"] = status_value
            
            if is_set:
                active_bits.append(f"Bit {bit_num}: {bit_desc}")
        
        # 특별한 레지스터에 대한 추가 처리
        additional_status = self._process_special_registers(register_info, raw_value, bit_status)
        
        return {
            'value': raw_value,
            'unit': '',
            'description': description,
            'raw_value': raw_value,
            'type': 'bitmask',
            'active_bits': active_bits,
            'bit_status': bit_status,
            'status_values': status_values,
            'additional_status': additional_status,
            'total_active': len(active_bits),
            'bit_flags': bin(raw_value)[2:].zfill(16),
            'decimal_value': raw_value,  # Decimal 값 명시적 표시
            'hex_value': f"0x{raw_value:04X}",  # HEX 값도 참고용으로 표시
            'interpretation': f"Decimal {raw_value} = Binary {bin(raw_value)[2:].zfill(16)} = Hex 0x{raw_value:04X}"
        }
    
    def _interpret_bit_status(self, bit_num: int, is_set: bool, bit_desc: str, raw_value: int) -> Optional[Dict[str, Any]]:
        """
        비트 상태를 해석하여 구체적인 값을 반환합니다.
        
        Args:
            bit_num: 비트 번호
            is_set: 비트가 설정되었는지 여부
            bit_desc: 비트 설명
            raw_value: 원시 값
            
        Returns:
            해석된 상태 정보 또는 None
        """
        # Fire Alarm 특별 처리
        if "Fire Alarm" in bit_desc:
            return {
                'status': '화재 경보' if is_set else '정상',
                'code': 1 if is_set else 0,
                'description': '화재 경보 상태'
            }
        
        # Smoke Sensor 특별 처리
        elif "Smoke Sensor" in bit_desc:
            return {
                'status': '연기 감지' if is_set else '정상',
                'code': 1 if is_set else 0,
                'description': '연기 센서 상태'
            }
        
        # 일반적인 비트 상태 처리 - 대괄호 안의 설명 파싱
        elif "[" in bit_desc and "]" in bit_desc:
            try:
                # 대괄호 안의 내용 추출
                start = bit_desc.find('[')
                end = bit_desc.find(']')
                if start != -1 and end != -1:
                    status_text = bit_desc[start+1:end]
                    parts = status_text.split('/')
                    
                    if len(parts) == 2:
                        # "0: Normal" 형태 파싱
                        false_part = parts[0].strip()
                        true_part = parts[1].strip()
                        
                        false_value = false_part.split(':', 1)[1].strip() if ':' in false_part else false_part
                        true_value = true_part.split(':', 1)[1].strip() if ':' in true_part else true_part
                        
                        return {
                            'status': true_value if is_set else false_value,
                            'code': 1 if is_set else 0,
                            'description': bit_desc.split('[')[0].strip()
                        }
            except:
                pass
        
        # 알람/에러 관련 처리
        elif any(keyword in bit_desc.lower() for keyword in ['alarm', 'error', 'fault', 'warning']):
            return {
                'status': '경고/에러' if is_set else '정상',
                'code': 1 if is_set else 0,
                'description': bit_desc
            }
        
        # 온도 관련 처리
        elif any(keyword in bit_desc.lower() for keyword in ['temperature', 'temp', '온도']):
            return {
                'status': '온도 이상' if is_set else '온도 정상',
                'code': 1 if is_set else 0,
                'description': bit_desc
            }
        
        # 전압 관련 처리
        elif any(keyword in bit_desc.lower() for keyword in ['voltage', 'volt', '전압']):
            return {
                'status': '전압 이상' if is_set else '전압 정상',
                'code': 1 if is_set else 0,
                'description': bit_desc
            }
        
        # 전류 관련 처리
        elif any(keyword in bit_desc.lower() for keyword in ['current', '전류']):
            return {
                'status': '전류 이상' if is_set else '전류 정상',
                'code': 1 if is_set else 0,
                'description': bit_desc
            }
        
        # 기본 처리 - Reserved나 기타
        if "Reserved" in bit_desc or "reserved" in bit_desc.lower():
            return {
                'status': '예약됨',
                'code': 1 if is_set else 0,
                'description': bit_desc
            }
        
        # 최종 기본값
        return {
            'status': '활성' if is_set else '비활성',
            'code': 1 if is_set else 0,
            'description': bit_desc
        }
    
    def _process_special_registers(self, register_info: Dict[str, Any], raw_value: int, bit_status: Dict[str, Any]) -> Dict[str, Any]:
        """
        특별한 레지스터에 대한 추가 처리를 수행합니다.
        
        Args:
            register_info: 레지스터 정보
            raw_value: 원시 값
            bit_status: 비트 상태 정보
            
        Returns:
            추가 상태 정보
        """
        additional_status = {}
        
        # Fire Alarm 레지스터 특별 처리
        if "Fire Alarm" in register_info.get('description', ''):
            # 비트 0: Smoke Sensor Status
            if bit_status.get('bit_00', {}).get('active', False):
                additional_status['smoke_sensor'] = {
                    'code': 1,
                    'text': '고장',
                    'description': 'Smoke Sensor Status'
                }
            else:
                additional_status['smoke_sensor'] = {
                    'code': 0,
                    'text': '정상',
                    'description': 'Smoke Sensor Status'
                }
            
            # 비트 15: Fire Alarm
            if bit_status.get('bit_15', {}).get('active', False):
                additional_status['fire_alarm'] = {
                    'code': 1,
                    'text': '화재 경보',
                    'description': 'Fire Alarm'
                }
            else:
                additional_status['fire_alarm'] = {
                    'code': 0,
                    'text': '정상',
                    'description': 'Fire Alarm'
                }
        
        return additional_status

    def _calculate_derived_values(self, processed_data: Dict[str, Any]):
        """
        BMS 특화 계산값들을 추가합니다.
        
        Args:
            processed_data: 가공된 데이터 딕셔너리 (수정됨)
        """
        try:
            # 셀 전압 차이 계산
            if 'battery_cell_max_voltage' in processed_data and 'battery_cell_min_voltage' in processed_data:
                voltage_diff = (
                    processed_data['battery_cell_max_voltage']['value'] - 
                    processed_data['battery_cell_min_voltage']['value']
                )
                processed_data['cell_voltage_diff'] = {
                    'value': round(voltage_diff, 3),
                    'unit': 'V',
                    'description': '셀 전압 차이 (최대-최소)',
                    'raw_value': voltage_diff,
                    'type': 'calculated'
                }
            
            # 모듈 온도 차이 계산
            if 'module_max_temperature' in processed_data and 'module_min_temperature' in processed_data:
                temp_diff = (
                    processed_data['module_max_temperature']['value'] - 
                    processed_data['module_min_temperature']['value']
                )
                processed_data['module_temp_diff'] = {
                    'value': round(temp_diff, 1),
                    'unit': '°C',
                    'description': '모듈 온도 차이 (최대-최소)',
                    'raw_value': temp_diff,
                    'type': 'calculated'
                }
            
            # 순간 전력 계산 (전압 * 전류)
            if 'rack_voltage' in processed_data and 'rack_current' in processed_data:
                instantaneous_power = (
                    processed_data['rack_voltage']['value'] * 
                    processed_data['rack_current']['value']
                )
                processed_data['instantaneous_power'] = {
                    'value': round(instantaneous_power, 2),
                    'unit': 'W',
                    'description': '순간 전력 (랙 전압 × 랙 전류)',
                    'raw_value': instantaneous_power,
                    'type': 'calculated'
                }
            
            # SOC 상태 해석
            if 'battery_soc' in processed_data:
                soc_value = processed_data['battery_soc']['value']
                if soc_value >= 80:
                    soc_status = '높음'
                    soc_level = 'HIGH'
                elif soc_value >= 50:
                    soc_status = '보통'
                    soc_level = 'NORMAL'
                elif soc_value >= 20:
                    soc_status = '낮음'
                    soc_level = 'LOW'
                else:
                    soc_status = '매우 낮음'
                    soc_level = 'CRITICAL'
                
                processed_data['soc_status'] = {
                    'value': soc_status,
                    'unit': '',
                    'description': 'SOC 상태',
                    'raw_value': soc_value,
                    'type': 'status',
                    'level': soc_level
                }
            
            # 시스템 운영 모드 해석
            if 'battery_system_operation_mode' in processed_data:
                mode_value = processed_data['battery_system_operation_mode']['raw_value']
                mode_status = []
                
                if mode_value & 0x01:
                    mode_status.append('초기화 완료')
                else:
                    mode_status.append('초기화 중')
                    
                if mode_value & 0x02:
                    mode_status.append('충전 중')
                if mode_value & 0x04:
                    mode_status.append('방전 중')
                if mode_value & 0x08:
                    mode_status.append('대기 (릴레이 ON)')
                
                processed_data['system_mode_status'] = {
                    'value': ', '.join(mode_status) if mode_status else '알 수 없음',
                    'unit': '',
                    'description': '시스템 운영 모드',
                    'raw_value': mode_value,
                    'type': 'status'
                }
            
            # 알람 및 에러 상태 요약
            alarm_count = 0
            error_count = 0
            warning_count = 0
            
            for key, data in processed_data.items():
                if data.get('type') == 'bitmask':
                    active_bits = data.get('active_bits', [])
                    if 'alarm' in key.lower():
                        alarm_count += len(active_bits)
                    elif 'error' in key.lower():
                        error_count += len(active_bits)
                    elif 'warning' in key.lower():
                        warning_count += len(active_bits)
            
            processed_data['system_health_summary'] = {
                'value': f'알람: {alarm_count}, 에러: {error_count}, 경고: {warning_count}',
                'unit': '',
                'description': '시스템 건강 상태 요약',
                'raw_value': {'alarms': alarm_count, 'errors': error_count, 'warnings': warning_count},
                'type': 'summary'
            }
                
        except Exception as e:
            self.logger.warning(f"BMS 파생값 계산 중 오류: {e}")
    
    async def control_dc_contactor(self, enable: bool) -> bool:
        """
        DC 접촉기 제어
        
        Args:
            enable: True=ON, False=OFF
            
        Returns:
            성공 여부
        """
        value = 1 if enable else 0
        result = await self.write_register('dc_contactor_control', value)
        
        if result:
            status = "ON" if enable else "OFF"
            self.logger.info(f"BMS DC 접촉기 {status} 명령 전송됨")
        
        return result
    
    async def reset_errors(self) -> bool:
        """에러 리셋 명령 (HEX 0x0050 = Decimal 80)"""
        self.logger.info(f"BMS 에러 리셋 명령 전송: 0x0050 (Decimal: {0x0050})")
        return await self.write_register('error_reset', 80)
    
    async def reset_system_lock(self) -> bool:
        """시스템 락 리셋 명령 (HEX 0x0050 = Decimal 80)"""
        self.logger.info(f"BMS 시스템 락 리셋 명령 전송: 0x0050 (Decimal: {0x0050})")
        return await self.write_register('system_lock_reset', 80)
    
    async def __aenter__(self):
        """비동기 컨텍스트 매니저 진입"""
        await self._connect_modbus()
        return self
    
    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """비동기 컨텍스트 매니저 종료"""
        await self._disconnect_modbus()
    
    async def handle_control_message(self, payload: Dict[str, Any]):
        """
        MQTT 제어 메시지를 처리합니다.
        지원 명령:
          - dc_contactor : { "command": "dc_contactor", "enable": true/false }
          - reset_errors : { "command": "reset_errors" }
          - reset_system_lock : { "command": "reset_system_lock" }
        """
        try:
            command = payload.get("command")
            
            if command == "dc_contactor":
                enable = bool(payload.get("enable", True))
                result = await self.control_dc_contactor(enable)
                status = "ON" if enable else "OFF"
                self.logger.info(f"BMS DC 접촉기 {status} 명령 {'성공' if result else '실패'}")
                
            elif command == "reset_errors":
                result = await self.reset_errors()
                self.logger.info(f"BMS 에러 리셋 {'성공' if result else '실패'}")
                
            elif command == "reset_system_lock":
                result = await self.reset_system_lock()
                self.logger.info(f"BMS 시스템 락 리셋 {'성공' if result else '실패'}")
                
            else:
                self.logger.warning(f"알 수 없는 BMS 제어 명령: {payload}")
                
        except Exception as e:
            self.logger.error(f"BMS 제어 메시지 처리 중 오류: {e}") 

    async def _ensure_connection(self) -> bool:
        """연결을 확인하고, 끊겨있으면 재연결을 시도하는 헬퍼 함수"""
        if self._connection_pool._pool_initialized and self.connected:
            return True
        
        self.logger.debug("연결이 끊겨있어 재연결을 시도합니다.")
        return await self._connect_modbus() 