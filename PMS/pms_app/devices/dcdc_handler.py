"""
DCDC 컨버터 핸들러
DCDC 장비에 특화된 데이터 읽기 및 처리 로직

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
        """새 연결 생성 - 연결 안정성 강화"""
        if self._created_connections >= self.max_connections:
            return None
            
        try:
            client = AsyncModbusTcpClient(
                host=self.host,
                port=self.port,
                timeout=self.timeout
            )
            
            # 연결 시도
            success = await asyncio.wait_for(client.connect(), timeout=self.timeout)
            if success and client.connected:
                # TCP Keep-Alive는 pymodbus 내부 구현으로 인해 설정이 어려움
                # 대신 연결 타임아웃과 재연결 로직으로 안정성 확보
                
                self._connections.add(client)
                self._created_connections += 1
                return client
            else:
                client.close()
                return None
        except Exception:
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


class DCDCHandler(DeviceInterface):
    """DCDC 컨버터 핸들러 클래스"""
    
    def __init__(self, device_config: Dict[str, Any], mqtt_client, system_config: Dict[str, Any]):
        """DCDC 핸들러 초기화"""
        super().__init__(device_config, mqtt_client, system_config)
        
        # Connection Pool 초기화 - AsyncPG 패턴
        self._connection_pool = ModbusConnectionPool(
            host=self.ip,
            port=self.port,
            max_connections=3,
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
            self.logger.info(f"🏊 DCDC Connection Pool 초기화 완료: {self.ip}")
            return True
        except Exception as e:
            self.logger.error(f"❌ DCDC Connection Pool 초기화 실패: {e}")
            return False
    
    def _group_consecutive_registers(self, section_data: Dict[str, Any]) -> List[Dict[str, Any]]:
        """연속된 레지스터들을 청크로 그룹화 (최대 120 Words)"""
        # 읽기 가능한 레지스터만 필터링 (0x03, 0x04)
        readable_registers = {}
        for key, register_info in section_data.items():
            function_code = register_info.get('function_code', '0x03')
            if function_code in ['0x03', '0x04']:
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

    def _start_queue_worker(self):
        """Request Queue Worker 시작 - Taskiq Worker 패턴 강화"""
        # 기존 worker가 정상 실행 중인지 확인
        if self._queue_worker_running and self._queue_worker_task and not self._queue_worker_task.done():
            self.logger.debug(f"🔄 DCDC Queue Worker 이미 실행 중: {self.ip}")
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
            self.logger.info(f"🚀 DCDC Request Queue Worker 시작/재시작: {self.ip}")
        except RuntimeError:
            # 이벤트 루프가 실행되지 않은 경우
            self.logger.warning(f"⏰ DCDC Queue Worker 시작 실패 - 이벤트 루프 없음: {self.ip}")
            try:
                self._queue_worker_task = asyncio.create_task(self._queue_worker())
                self._queue_worker_running = True
                self.logger.info(f"🚀 DCDC Request Queue Worker 시작 (create_task): {self.ip}")
            except Exception as e:
                self.logger.error(f"❌ DCDC Queue Worker 시작 실패: {e}")
    
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
                            self.logger.warning(f"⚠️ DCDC Queue Worker 예외로 종료됨: {exception}")
                        else:
                            self.logger.info(f"ℹ️ DCDC Queue Worker 정상 종료됨")
                    except:
                        pass
                
                self.logger.warning(f"🔄 DCDC Queue Worker 중단됨 - 재시작 시도: {self.ip}")
                self._start_queue_worker()
                
        except Exception as e:
            self.logger.error(f"❌ DCDC Queue Worker 상태 확인 중 오류: {e}")
    
    async def _queue_worker(self):
        """Request Queue 처리 워커 - Taskiq + 배치 처리 패턴"""
        self.logger.info(f"🔄 DCDC Queue Worker 실행 시작 (배치 처리 지원): {self.ip}")
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
                    self.logger.error(f"❌ DCDC Queue Worker invalid state 오류: {e}")
                    await self._handle_connection_error()
                else:
                    self.logger.error(f"❌ DCDC Queue Worker 배치 처리 오류 #{consecutive_errors}: {e}")
                
                # 연속 오류가 많을 때 복구 시도
                if consecutive_errors >= max_consecutive_errors:
                    self.logger.warning(f"⚠️ DCDC 연속 오류 {consecutive_errors}회, 복구 시도")
                    await self._handle_connection_error()
                    await asyncio.sleep(2.0)
                    consecutive_errors = 0
                
                await asyncio.sleep(0.5 + (consecutive_errors * 0.5))
        
        self.logger.info(f"🛑 DCDC Queue Worker 종료: {self.ip}")
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
            self.logger.info(f"🔄 DCDC 연결 오류 복구 시작: {self.ip}")
            
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
                self.logger.info(f"✅ DCDC 연결 복구 성공: {self.ip}")
            else:
                self.logger.warning(f"❌ DCDC 연결 복구 실패: {self.ip}")
                
        except Exception as e:
            self.logger.error(f"❌ DCDC 연결 복구 중 오류: {e}")
    
    async def _execute_read_request_with_client(self, client: AsyncModbusTcpClient, request: Dict[str, Any]) -> bool:
        """클라이언트를 사용한 READ 요청 실행"""
        address = request.get('address', 0)
        count = request.get('count', 1)
        slave_id = request.get('slave_id', self.slave_id)
        function_code = request.get('function_code', '0x03')
        future = request['future']
        
        try:
            if not client or not client.connected:
                future.set_result(None)
                return False
                            
            # Function Code에 따른 읽기
            if function_code == '0x03':
                # Read Holding Registers
                response = await asyncio.wait_for(
                    client.read_holding_registers(
                        address=address, count=count, slave=slave_id
                    ),
                    timeout=3.0
                )
            elif function_code == '0x04':
                # Read Input Registers
                response = await asyncio.wait_for(
                    client.read_input_registers(
                        address=address, count=count, slave=slave_id
                    ),
                    timeout=3.0
                )
            else:
                self.logger.warning(f"지원하지 않는 Function Code: {function_code}")
                future.set_result(None)
                return False
                            
            if response.isError():
                future.set_result(None)
                return False
            else:
                future.set_result(response)
                return True
                
        except asyncio.TimeoutError:
            self.logger.warning(f"❌ DCDC READ 타임아웃 (주소={address})")
            future.set_result(None)
            return False
        except Exception as e:
            self.logger.debug(f"DCDC READ 오류 (주소={address}): {e}")
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
                self.logger.error(f"❌ DCDC WRITE 오류: {response}")
                future.set_result(False)
                return False
            else:
                self.logger.info(f"✅ DCDC WRITE 성공: 주소={address}, 값={value}")
                future.set_result(True)
                return True
                
        except asyncio.TimeoutError:
            self.logger.warning(f"❌ DCDC WRITE 타임아웃 (주소={address})")
            future.set_result(False)
            return False
        except Exception as e:
            self.logger.error(f"❌ DCDC WRITE 오류: {e}")
            future.set_result(False)
            return False
    
    async def _queue_read_register(self, address: int, count: int = 1, function_code: str = '0x03'):
        """Request Queue를 통한 READ 요청"""
        # Future 객체 생성
        future = asyncio.Future()
        
        # Request 생성
        request = {
            'type': 'read',
            'address': address,
            'count': count,
            'slave_id': self.slave_id,
            'function_code': function_code,
            'future': future
        }
        
        # 큐에 요청 추가
        await self._request_queue.put(request)
        
        # 결과 대기 (최대 5초)
        try:
            result = await asyncio.wait_for(future, timeout=5.0)
            return result
        except asyncio.TimeoutError:
            self.logger.error(f"❌ DCDC READ 타임아웃: 주소={address}")
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
            self.logger.error(f"❌ DCDC WRITE 타임아웃: 주소={address}, 값={value}")
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
            
                    self.logger.debug(f"✅ DCDC Modbus 연결 성공: {self.ip}:{self.port}")
                    return True
                else:
                    self.connected = False
                    self.logger.warning(f"❌ DCDC Modbus 연결 실패: {self.ip}:{self.port}")
                    return False
            
            except Exception as e:
                self.logger.error(f"❌ DCDC Modbus 연결 중 오류: {e}")
                self.connected = False
                return False

    async def _disconnect_modbus(self):
        """Modbus TCP 연결 해제"""
        try:
            self.connected = False
            await self._connection_pool.close_all()
            self.logger.debug("DCDC Modbus 연결 해제됨")
        except Exception as e:
            self.logger.warning(f"DCDC Modbus 연결 해제 중 오류: {e}")
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

    async def read_data(self) -> Optional[Dict[str, Any]]:
        """
        DCDC 장비에서 데이터를 읽어옵니다.
        청크(블록) 읽기로 최적화된 데이터 읽기
        Function Code 0x03/0x04 사용
        
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
                total_chunks = 0
                successful_chunks = 0
                
                # 각 섹션별로 청크 읽기
                sections = ['parameter_registers', 'metering_registers', 'optional_metering_registers']
                
                for section_name in sections:
                    section_registers = self.device_map.get(section_name, {})
                    chunks = self._group_consecutive_registers(section_registers)
                    
                    for chunk in chunks:
                        total_chunks += 1
                        try:
                            # 첫 번째 레지스터의 Function Code 사용
                            if chunk['registers']:
                                first_register = chunk['registers'][0][1]
                                function_code = first_register.get('function_code', '0x03')
                            else:
                                function_code = '0x03'
                            
                            # 청크 단위로 읽기
                            response = await self._queue_read_register(
                                chunk['start_address'], 
                                chunk['count'],
                                function_code
                            )
                            
                            if response is None or response.isError():
                                self.logger.debug(f"청크 읽기 실패 - 주소:{chunk['start_address']}, 크기:{chunk['count']}")
                                continue
                            
                            successful_chunks += 1
                            
                                                        # 청크 내 각 레지스터 값 추출
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
                                    
                                    raw_data[key] = raw_value
                                    
                                except Exception as e:
                                    self.logger.debug(f"레지스터 값 추출 오류 - {key}: {e}")
                                    continue
                            
                        except Exception as e:
                            self.logger.debug(f"청크 읽기 오류: {e}")
                            continue
                
                if raw_data:
                    efficiency = (successful_chunks / total_chunks * 100) if total_chunks > 0 else 0
                    self.logger.debug(f"DCDC 청크 읽기 완료: {len(raw_data)}개 레지스터, {successful_chunks}/{total_chunks} 청크 성공 ({efficiency:.1f}%)")
                    return raw_data
                else:
                    self.logger.warning("DCDC에서 읽어온 데이터가 없습니다")
                    return None
                
            except ModbusException as e:
                self.logger.error(f"DCDC Modbus 예외 발생: {e}")
                await self._disconnect_modbus()
                return None
            except Exception as e:
                self.logger.error(f"DCDC 데이터 읽기 중 예외 발생: {e}")
                return None

    async def _ensure_connection(self) -> bool:
        """연결을 확인하고, 끊겨있으면 재연결을 시도하는 헬퍼 함수"""
        if self._connection_pool._pool_initialized and self.connected:
            return True
        
        self.logger.debug("연결이 끊겨있어 재연결을 시도합니다.")
        return await self._connect_modbus()
    
    async def __aenter__(self):
        """비동기 컨텍스트 매니저 진입"""
        await self._connect_modbus()
        return self
    
    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """비동기 컨텍스트 매니저 종료"""
        await self._disconnect_modbus()

    async def write_register(self, register_name: str, value: int) -> bool:
        """
        지정된 레지스터에 값을 씁니다.
        독립적인 Write 전용 클라이언트를 사용하여 폴링과 완전히 분리합니다.
        
        Args:
            register_name: 쓰기를 원하는 레지스터의 이름 (맵 파일 기준)
            value: 쓸 값
            
        Returns:
            성공 여부 (True/False)
        """
        self.logger.info(f"🔥 DCDC write_register 시작: {register_name} = {value}")
        
        # 📝 Queue Worker 상태 확인 및 자동 재시작
        self._ensure_queue_worker_running()
        
        # 레지스터 정보 확인
        all_registers = {
            **self.device_map.get('parameter_registers', {}),
            **self.device_map.get('control_registers', {})
        }
        
        if register_name not in all_registers:
            self.logger.error(f"❌ 알 수 없는 DCDC 레지스터 이름: {register_name}")
            return False
        
        register_info = all_registers[register_name]
        address = register_info['address']
        
        # Request Queue를 사용하여 순차 WRITE 처리
        return await self._queue_write_register(address, value)
    
    async def set_operation_mode(self, mode: str) -> bool:
        """
        DCDC 운전 모드 설정 (실제 맵 파일의 명령 레지스터 사용)
        
        Args:
            mode: 'stop'(정지), 'ready'(대기), 'charge'(충전), 'regen'(방전), 'start'(독립운전), 'standby'(대기모드), 'solar'(발전) 중 하나
            
        Returns:
            성공 여부
        """
        # 각 모드에 맞는 명령 레지스터와 값 매핑 (실제 맵 파일 기준)
        mode_commands = {
            'stop': ('stop_command', 85),         # 정지
            'ready': ('ready_command', 85),       # 대기운전
            'charge': ('charge_command', 85),     # 충전운전
            'regen': ('regen_command', 85),       # 방전운전
            'start': ('start_command', 85),       # 독립운전
            'standby': ('ready_standby_command', 1), # 대기모드
            'solar': ('solar_command', 1)           # 발전모드 (PV인버터)
        }
        
        if mode not in mode_commands:
            self.logger.error(f"지원하지 않는 운전 모드: {mode}. 지원 모드: {list(mode_commands.keys())}")
            return False
        
        register_name, value = mode_commands[mode]
        result = await self.write_register(register_name, value)
        
        if result:
            self.logger.info(f"DCDC 운전 모드 '{mode}' 설정 성공")
        else:
            self.logger.error(f"DCDC 운전 모드 '{mode}' 설정 실패")
            
        return result
    
    async def reset_faults(self) -> bool:
        """
        DCDC 고장 리셋 (실제 맵 파일의 reset_command 사용)
        
        Returns:
            성공 여부
        """
        result = await self.write_register('reset_command', 85)
        
        if result:
            self.logger.info("DCDC 고장 리셋 성공")
        else:
            self.logger.error("DCDC 고장 리셋 실패")
            
        return result
    
    # PV 인버터 제어 함수들 추가
    async def pv_reset(self) -> bool:
        """PV 인버터 리셋"""
        return await self.reset_faults()
    
    async def pv_stop(self) -> bool:
        """PV 인버터 정지(그만)"""
        return await self.set_operation_mode('stop')
    
    async def pv_ready(self) -> bool:
        """PV 인버터 대기"""
        return await self.set_operation_mode('ready')
    
    async def pv_solar(self) -> bool:
        """PV 인버터 발전(Solar)"""
        return await self.set_operation_mode('solar')
    
    async def set_current_reference(self, current_a: float) -> bool:
        """
        DCDC 출력 전류 설정점 설정
        주의: 현재 맵 파일에 current_reference 레지스터가 없어 사용 불가
        
        Args:
            current_a: 설정할 전류값 (A)
            
        Returns:
            성공 여부
        """
        self.logger.warning("현재 DCDC 맵 파일에 전류 설정점 레지스터가 정의되지 않았습니다.")
        return False
    
    async def set_voltage_reference(self, voltage_v: float) -> bool:
        """
        DCDC 출력 전압 설정점 설정
        주의: 현재 맵 파일에 voltage_reference 레지스터가 없어 사용 불가
        
        Args:
            voltage_v: 설정할 전압값 (V)
            
        Returns:
            성공 여부
        """
        self.logger.warning("현재 DCDC 맵 파일에 전압 설정점 레지스터가 정의되지 않았습니다.")
        return False
    
    async def handle_control_message(self, payload: Dict[str, Any]):
        """
        MQTT 제어 메시지를 처리합니다.
        지원 명령:
          - operation_mode : { "command": "operation_mode", "mode": "stop/ready/charge/regen/start/standby/solar" }
          - reset_faults : { "command": "reset_faults" }
          - pv_reset : { "command": "pv_reset" }
          - pv_stop : { "command": "pv_stop" }
          - pv_ready : { "command": "pv_ready" }
          - pv_solar : { "command": "pv_solar" }
        """
        try:
            command = payload.get("command")
            
            if command == "operation_mode":
                mode = payload.get("mode")
                if mode:
                    result = await self.set_operation_mode(mode)
                    self.logger.info(f"DCDC 운전 모드 설정 {'성공' if result else '실패'}: {mode}")
                else:
                    self.logger.warning("운전 모드가 지정되지 않았습니다")
            
            elif command == "reset_faults":
                result = await self.reset_faults()
                self.logger.info(f"DCDC 고장 리셋 {'성공' if result else '실패'}")
            
            elif command == "pv_reset":
                result = await self.pv_reset()
                self.logger.info(f"PV 인버터 리셋 {'성공' if result else '실패'}")
            
            elif command == "pv_stop":
                result = await self.pv_stop()
                self.logger.info(f"PV 인버터 정지 {'성공' if result else '실패'}")
            
            elif command == "pv_ready":
                result = await self.pv_ready()
                self.logger.info(f"PV 인버터 대기 {'성공' if result else '실패'}")
            
            elif command == "pv_solar":
                result = await self.pv_solar()
                self.logger.info(f"PV 인버터 발전 시작 {'성공' if result else '실패'}")
            
            # 레거시 명령들 (현재 사용 불가)
            elif command == "current_reference":
                current_a = payload.get("current_a")
                if current_a is not None:
                    result = await self.set_current_reference(float(current_a))
                    self.logger.info(f"DCDC 출력 전류 설정 {'성공' if result else '실패'}: {current_a}A")
                else:
                    self.logger.warning("출력 전류값이 지정되지 않았습니다")
            
            elif command == "voltage_reference":
                voltage_v = payload.get("voltage_v")
                if voltage_v is not None:
                    result = await self.set_voltage_reference(float(voltage_v))
                    self.logger.info(f"DCDC 출력 전압 설정 {'성공' if result else '실패'}: {voltage_v}V")
                else:
                    self.logger.warning("출력 전압값이 지정되지 않았습니다")
            
            else:
                self.logger.warning(f"알 수 없는 DCDC 제어 명령: {payload}")
                
        except Exception as e:
            self.logger.error(f"DCDC 제어 메시지 처리 중 오류: {e}") 

    async def process_data(self, raw_data: Dict[str, Any]) -> Dict[str, Any]:
        """
        DCDC 원시 데이터를 가공합니다.
        
        Args:
            raw_data: 원시 데이터 딕셔너리
            
        Returns:
            가공된 데이터 딕셔너리
        """
        processed_data = {}
        
        # 모든 레지스터 섹션을 확인
        all_registers = {}
        for section in ['parameter_registers', 'metering_registers', 'control_registers', 'optional_metering_registers']:
            if section in self.device_map:
                all_registers.update(self.device_map[section])
        
        try:
            for key, raw_value in raw_data.items():
                if key in all_registers:
                    register_info = all_registers[key]
                    scale = register_info.get('scale', 1)
                    unit = register_info.get('unit', '')
                    description = register_info.get('description', key)
                    data_type = register_info.get('type', 'value')
                    
                    if data_type == 'bitmask':
                        # 비트마스크 처리
                        processed_data[key] = self._process_bitmask(raw_value, register_info, description)
                    else:
                        # 일반 값 처리
                        processed_value = raw_value * scale
                        processed_data[key] = {
                            'value': processed_value,
                            'unit': unit,
                            'description': description,
                            'raw_value': raw_value
                        }
                else:
                    # 맵에 없는 데이터는 원시값 그대로
                    processed_data[key] = {
                        'value': raw_value,
                        'unit': '',
                        'description': key,
                        'raw_value': raw_value
                    }
            
            # DCDC 특화 계산
            self._calculate_derived_values(processed_data)
            
            self.logger.debug(f"DCDC 데이터 가공 완료: {len(processed_data)}개 항목")
            return processed_data
            
        except Exception as e:
            self.logger.error(f"DCDC 데이터 가공 중 오류: {e}")
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
        
        return {
            'value': raw_value,
            'unit': '',
            'description': description,
            'raw_value': raw_value,
            'type': 'bitmask',
            'active_bits': active_bits,
            'bit_status': bit_status,
            'status_values': status_values,
            'total_active': len(active_bits)
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
        # 일반적인 비트 상태 처리 - 대괄호 안의 설명 파싱
        if "[" in bit_desc and "]" in bit_desc:
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
        
        # 기본 처리 - Reserved나 기타
        if "Reserved" in bit_desc:
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
    
    def _calculate_derived_values(self, processed_data: Dict[str, Any]):
        """
        DCDC 특화 계산값들을 추가합니다.
        
        Args:
            processed_data: 가공된 데이터 딕셔너리 (수정됨)
        """
        try:
            # 입력 전력 계산 (DC 입력 전압 * 전류)
            if 'dc_input_voltage' in processed_data and 'dc_input_current' in processed_data:
                input_power = (
                    processed_data['dc_input_voltage']['value'] * 
                    processed_data['dc_input_current']['value']
                )
                processed_data['dc_input_power'] = {
                    'value': round(input_power, 2),
                    'unit': 'W',
                    'description': 'DC 입력 전력',
                    'raw_value': input_power
                }
            
            # 출력 전력 계산 (DC 출력 전압 * 전류)
            if 'dc_output_voltage' in processed_data and 'dc_output_current' in processed_data:
                output_power = (
                    processed_data['dc_output_voltage']['value'] * 
                    processed_data['dc_output_current']['value']
                )
                processed_data['dc_output_power'] = {
                    'value': round(output_power, 2),
                    'unit': 'W',
                    'description': 'DC 출력 전력',
                    'raw_value': output_power
                }
            
            # DCDC 효율 계산 (출력 전력 / 입력 전력)
            if ('dc_input_power' in processed_data and 'dc_output_power' in processed_data and 
                processed_data['dc_input_power']['value'] > 0):
                
                efficiency = (processed_data['dc_output_power']['value'] / 
                            processed_data['dc_input_power']['value']) * 100
                
                processed_data['dcdc_efficiency'] = {
                    'value': round(min(efficiency, 100), 2),  # 100% 초과 방지
                    'unit': '%',
                    'description': 'DCDC 변환 효율',
                    'raw_value': efficiency
                }
                
        except Exception as e:
            self.logger.warning(f"DCDC 파생값 계산 중 오류: {e}") 