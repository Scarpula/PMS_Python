"""
DB 설정 로더
PostgreSQL 데이터베이스에서 자동운전 모드 설정을 불러오는 모듈
"""

import asyncio
import logging
from typing import Dict, Any, Optional
import asyncpg
from datetime import datetime


class DBConfigLoader:
    """데이터베이스에서 설정을 로드하는 클래스"""
    
    def __init__(self, db_url: str, device_location: str):
        """
        DB 설정 로더 초기화
        
        Args:
            db_url: PostgreSQL 연결 URL
            device_location: 장비 위치 (config.yml에서 로드됨)
        """
        self.db_url = db_url
        self.device_location = device_location
        self.logger = logging.getLogger(self.__class__.__name__)
        
    async def load_auto_mode_config(self) -> Dict[str, Any]:
        """
        DB에서 자동운전 모드 설정을 로드
        
        Returns:
            자동운전 모드 설정 딕셔너리
        """
        try:
            self.logger.info(f"🔍 DB에서 '{self.device_location}' 자동운전 설정 로드 중...")
            
            # DB 연결
            conn = await asyncpg.connect(self.db_url)
            
            try:
                # 디버깅: 테이블 스키마 정보 확인
                try:
                    schema_query = """
                    SELECT column_name, data_type, is_nullable
                    FROM information_schema.columns 
                    WHERE table_name = 'DEVICE_LOCATION_STATUS'
                    ORDER BY ordinal_position
                    """
                    schema_rows = await conn.fetch(schema_query)
                    self.logger.debug(f"🔍 테이블 스키마 정보:")
                    for schema_row in schema_rows:
                        self.logger.debug(f"   📋 {schema_row['column_name']}: {schema_row['data_type']} (null: {schema_row['is_nullable']})")
                except Exception as schema_e:
                    self.logger.debug(f"⚠️ 스키마 정보 조회 실패: {schema_e}")
                
                # 최신 설정 조회 (가장 최근 업데이트된 레코드)
                query = """
                SELECT 
                    "SOC_HIGH_THRESHOLD",
                    "SOC_LOW_THRESHOLD", 
                    "SOC_CHARGE_STOP_THRESHOLD",
                    "DCDC_STANDBY_TIME",
                    "CHARGING_POWER",
                    "OPERATION_MODE",
                    "AUTO_MODE_STATUS",
                    "AUTO_MODE_ACTIVE",
                    "UPDATED_AT"
                FROM "DEVICE_LOCATION_STATUS" 
                WHERE "DEVICE_LOCATION" = $1 AND "IS_ACTIVE" = true
                ORDER BY "UPDATED_AT" DESC 
                LIMIT 1
                """
                
                row = await conn.fetchrow(query, self.device_location)
                
                if row:
                    # PostgreSQL numeric 타입을 위한 안전한 데이터 변환 함수들
                    def safe_float(value, default):
                        """PostgreSQL numeric을 float로 안전하게 변환"""
                        try:
                            if value is None:
                                return default
                            
                            # 디버깅: 데이터 타입 로깅
                            self.logger.debug(f"🔍 safe_float 변환: {type(value).__name__} = {repr(value)}")
                            
                            # Decimal 객체 처리
                            if hasattr(value, '__float__'):
                                return float(value)
                            
                            # 문자열 처리 (PostgreSQL numeric이 문자열로 올 때)
                            if isinstance(value, str):
                                # 빈 문자열 체크
                                if not value.strip():
                                    return default
                                return float(value)
                            
                            # 이미 숫자인 경우
                            if isinstance(value, (int, float)):
                                return float(value)
                            
                            # bytes 처리 (혹시 bytes로 올 경우)
                            if isinstance(value, bytes):
                                try:
                                    decoded_value = value.decode('utf-8')
                                    return float(decoded_value) if decoded_value.strip() else default
                                except (UnicodeDecodeError, ValueError):
                                    self.logger.warning(f"⚠️ bytes 디코딩 실패: {repr(value)}")
                                    return default
                            
                            # dict나 list 같은 복잡한 타입 처리
                            if isinstance(value, (dict, list)):
                                self.logger.warning(f"⚠️ 복잡한 데이터 타입 감지 (float 변환): {type(value).__name__} = {repr(value)}")
                                return default
                            
                            # 예상하지 못한 타입
                            self.logger.warning(f"⚠️ 예상하지 못한 데이터 타입 (float 변환): {type(value).__name__} = {repr(value)}, 기본값 사용: {default}")
                            return default
                            
                        except (ValueError, TypeError, AttributeError) as e:
                            self.logger.warning(f"⚠️ Float 변환 실패: {repr(value)} -> {default}, 오류: {e}")
                            return default
                    
                    def safe_int(value, default):
                        """PostgreSQL integer를 int로 안전하게 변환"""
                        try:
                            if value is None:
                                return default
                            
                            # 디버깅: 데이터 타입 로깅
                            self.logger.debug(f"🔍 safe_int 변환: {type(value).__name__} = {repr(value)}")
                            
                            # 이미 정수인 경우
                            if isinstance(value, int):
                                return value
                            
                            # 문자열 처리
                            if isinstance(value, str):
                                if not value.strip():
                                    return default
                                # 소수점 있는 문자열은 float으로 먼저 변환 후 int
                                if '.' in value:
                                    return int(float(value))
                                return int(value)
                            
                            # float 처리
                            if isinstance(value, float):
                                return int(value)
                            
                            # bytes 처리
                            if isinstance(value, bytes):
                                try:
                                    decoded_value = value.decode('utf-8')
                                    return int(float(decoded_value)) if decoded_value.strip() else default
                                except (UnicodeDecodeError, ValueError):
                                    self.logger.warning(f"⚠️ bytes 디코딩 실패: {repr(value)}")
                                    return default
                            
                            # dict나 list 같은 복잡한 타입 처리
                            if isinstance(value, (dict, list)):
                                self.logger.warning(f"⚠️ 복잡한 데이터 타입 감지 (int 변환): {type(value).__name__} = {repr(value)}")
                                return default
                            
                            # 예상하지 못한 타입
                            self.logger.warning(f"⚠️ 예상하지 못한 데이터 타입 (int 변환): {type(value).__name__} = {repr(value)}, 기본값 사용: {default}")
                            return default
                            
                        except (ValueError, TypeError) as e:
                            self.logger.warning(f"⚠️ Int 변환 실패: {repr(value)} -> {default}, 오류: {e}")
                            return default
                    
                    def safe_str(value, default):
                        """모든 타입을 문자열로 안전하게 변환"""
                        try:
                            if value is None:
                                return default
                            
                            # 디버깅: 데이터 타입 로깅
                            self.logger.debug(f"🔍 safe_str 변환: {type(value).__name__} = {repr(value)}")
                            
                            # 이미 문자열인 경우
                            if isinstance(value, str):
                                return value
                            
                            # bytes 처리
                            if isinstance(value, bytes):
                                return value.decode('utf-8', errors='ignore')
                            
                            # 불린 처리
                            if isinstance(value, bool):
                                return str(value).lower()
                            
                            # dict나 list 같은 복잡한 타입 처리
                            if isinstance(value, (dict, list)):
                                self.logger.warning(f"⚠️ 복잡한 데이터 타입을 문자열로 변환: {type(value).__name__} = {repr(value)}")
                                return str(value)  # JSON 형태로 변환될 것
                            
                            # 기타 모든 타입
                            return str(value)
                            
                        except Exception as e:
                            self.logger.warning(f"⚠️ String 변환 실패: {repr(value)} -> {default}, 오류: {e}")
                            return default
                    
                    # 디버깅: DB row 전체 구조 로깅
                    self.logger.debug(f"🔍 DB row 타입: {type(row).__name__}")
                    self.logger.debug(f"🔍 DB row 키들: {list(row.keys()) if hasattr(row, 'keys') else 'keys() 메서드 없음'}")
                    
                    # 각 필드별로 안전하게 추출하고 로깅
                    try:
                        soc_high = row['SOC_HIGH_THRESHOLD']
                        self.logger.debug(f"🔍 SOC_HIGH_THRESHOLD: {type(soc_high).__name__} = {repr(soc_high)}")
                    except Exception as e:
                        self.logger.error(f"❌ SOC_HIGH_THRESHOLD 접근 실패: {e}")
                        soc_high = None
                    
                    # DB에서 읽은 자동 모드 상태 확인
                    auto_mode_active = bool(row.get('AUTO_MODE_ACTIVE')) if row.get('AUTO_MODE_ACTIVE') is not None else False
                    operation_mode = safe_str(row.get('OPERATION_MODE'), 'basic')
                    
                    # DB 상태를 GUI 형식으로 매핑 (auto_mode_enabled 키 추가)
                    auto_mode_enabled = auto_mode_active and (operation_mode == 'auto')
                    
                    config = {
                        'enabled': True,
                        'soc_high_threshold': safe_float(soc_high, 88.0),
                        'soc_low_threshold': safe_float(row.get('SOC_LOW_THRESHOLD'), 5.0),
                        'soc_charge_stop_threshold': safe_float(row.get('SOC_CHARGE_STOP_THRESHOLD'), 25.0),
                        'dcdc_standby_time': safe_int(row.get('DCDC_STANDBY_TIME'), 30),
                        'charging_power': safe_float(row.get('CHARGING_POWER'), 10.0),
                        'command_interval': 5,  # 기본값 (DB에 없는 항목)
                        'soc_monitor_interval': 2.0,  # 기본값 (DB에 없는 항목)
                        
                        # 🔧 GUI에서 요구하는 auto_mode_enabled 키 추가
                        'auto_mode_enabled': auto_mode_enabled,
                        
                        # DB에서 읽은 원본 상태 정보 (디버깅용)
                        'db_operation_mode': operation_mode,
                        'db_auto_mode_status': safe_str(row.get('AUTO_MODE_STATUS'), 'IDLE'),
                        'db_auto_mode_active': auto_mode_active,
                        'db_updated_at': row.get('UPDATED_AT') if row.get('UPDATED_AT') is not None else datetime.now()
                    }
                    
                    self.logger.info(f"✅ DB 설정 로드 성공:")
                    self.logger.info(f"   🔋 SOC 상한: {config['soc_high_threshold']}%")
                    self.logger.info(f"   🔋 SOC 하한: {config['soc_low_threshold']}%")
                    self.logger.info(f"   🔋 충전 정지: {config['soc_charge_stop_threshold']}%")
                    self.logger.info(f"   ⏱️ DCDC 대기: {config['dcdc_standby_time']}초")
                    self.logger.info(f"   ⚡ 충전 전력: {config['charging_power']}kW")
                    self.logger.info(f"   📊 운전 모드: {config['db_operation_mode']}")
                    self.logger.info(f"   🤖 자동 모드 상태: {config['db_auto_mode_status']}")
                    self.logger.info(f"   🎛️ 자동 모드 활성화: {config['auto_mode_enabled']}")
                    self.logger.info(f"   📅 업데이트: {config['db_updated_at']}")
                    
                    return config
                else:
                    # DB에 데이터가 없는 경우 기본값 사용
                    self.logger.warning(f"⚠️ '{self.device_location}' 설정이 DB에 없습니다. 기본값 사용")
                    return self._get_default_config()
                    
            finally:
                await conn.close()
                
        except Exception as e:
            import traceback
            self.logger.error(f"❌ DB 설정 로드 실패: {e}")
            self.logger.error(f"📍 오류 위치: {traceback.format_exc()}")
            self.logger.error(f"💡 기본 설정값으로 대체합니다")
            return self._get_default_config()
    
    def _get_default_config(self) -> Dict[str, Any]:
        """기본 설정값 반환"""
        return {
            'enabled': True,
            'soc_high_threshold': 88.0,
            'soc_low_threshold': 5.0,
            'soc_charge_stop_threshold': 25.0,
            'dcdc_standby_time': 30,
            'command_interval': 5,
            'soc_monitor_interval': 2.0,
            'charging_power': 10.0,
            
            # GUI 호환성을 위한 키
            'auto_mode_enabled': False,
            
            # 수동 상태 (원본 DB 정보)
            'db_operation_mode': 'basic',
            'db_auto_mode_status': 'IDLE',
            'db_auto_mode_active': False,
            'db_updated_at': datetime.now()
        }
    
    async def save_auto_mode_config(self, config: Dict[str, Any], user_id: str = "kim") -> bool:
        """
        자동운전 모드 설정을 DB에 저장
        
        Args:
            config: 저장할 설정 딕셔너리
            user_id: 사용자 ID
            
        Returns:
            저장 성공 여부
        """
        try:
            self.logger.info(f"💾 '{self.device_location}' 자동운전 설정 DB 저장 중...")
            
            # DB 연결
            conn = await asyncpg.connect(self.db_url)
            
            try:
                # UPSERT (INSERT ON CONFLICT UPDATE)
                upsert_query = """
                INSERT INTO "DEVICE_LOCATION_STATUS" (
                    "USER_ID", "DEVICE_LOCATION", 
                    "SOC_HIGH_THRESHOLD", "SOC_LOW_THRESHOLD", "SOC_CHARGE_STOP_THRESHOLD",
                    "DCDC_STANDBY_TIME", "CHARGING_POWER",
                    "OPERATION_MODE", "AUTO_MODE_STATUS", "AUTO_MODE_ACTIVE",
                    "UPDATED_AT", "LAST_MESSAGE_TIME"
                ) VALUES (
                    $1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12
                )
                ON CONFLICT ("USER_ID", "DEVICE_LOCATION") 
                DO UPDATE SET
                    "SOC_HIGH_THRESHOLD" = EXCLUDED."SOC_HIGH_THRESHOLD",
                    "SOC_LOW_THRESHOLD" = EXCLUDED."SOC_LOW_THRESHOLD",
                    "SOC_CHARGE_STOP_THRESHOLD" = EXCLUDED."SOC_CHARGE_STOP_THRESHOLD",
                    "DCDC_STANDBY_TIME" = EXCLUDED."DCDC_STANDBY_TIME",
                    "CHARGING_POWER" = EXCLUDED."CHARGING_POWER",
                    "OPERATION_MODE" = EXCLUDED."OPERATION_MODE",
                    "AUTO_MODE_STATUS" = EXCLUDED."AUTO_MODE_STATUS",
                    "AUTO_MODE_ACTIVE" = EXCLUDED."AUTO_MODE_ACTIVE",
                    "UPDATED_AT" = EXCLUDED."UPDATED_AT",
                    "LAST_MESSAGE_TIME" = EXCLUDED."LAST_MESSAGE_TIME"
                """
                
                now = datetime.now()
                
                # GUI에서 보낸 auto_mode_enabled를 DB 필드로 매핑
                auto_mode_enabled = config.get('auto_mode_enabled', False)
                operation_mode = 'auto' if auto_mode_enabled else 'basic'
                auto_mode_status = 'READY' if auto_mode_enabled else 'IDLE'
                
                self.logger.info(f"💾 DB 저장 매핑:")
                self.logger.info(f"   🎛️ auto_mode_enabled: {auto_mode_enabled}")
                self.logger.info(f"   📊 operation_mode: {operation_mode}")
                self.logger.info(f"   🤖 auto_mode_status: {auto_mode_status}")
                self.logger.info(f"   🔋 SOC 상한: {config.get('soc_high_threshold', 88.0)}%")
                self.logger.info(f"   🔋 SOC 하한: {config.get('soc_low_threshold', 5.0)}%")
                self.logger.info(f"   🔋 충전 정지: {config.get('soc_charge_stop_threshold', 25.0)}%")
                self.logger.info(f"   ⏱️ DCDC 대기: {config.get('dcdc_standby_time', 30)}분")
                self.logger.info(f"   ⚡ 충전 전력: {config.get('charging_power', 10.0)}kW")
                
                await conn.execute(
                    upsert_query,
                    user_id,
                    self.device_location,
                    config.get('soc_high_threshold', 88.0),
                    config.get('soc_low_threshold', 5.0),
                    config.get('soc_charge_stop_threshold', 25.0),
                    config.get('dcdc_standby_time', 30),
                    config.get('charging_power', 10.0),
                    operation_mode,
                    auto_mode_status,
                    auto_mode_enabled,
                    now,
                    now
                )
                
                self.logger.info(f"✅ DB 설정 저장 성공")
                return True
                
            finally:
                await conn.close()
                
        except Exception as e:
            self.logger.error(f"❌ DB 설정 저장 실패: {e}")
            return False
    
    async def test_connection(self) -> bool:
        """DB 연결 테스트"""
        try:
            self.logger.info(f"🔌 DB 연결 테스트 중...")
            conn = await asyncpg.connect(self.db_url)
            await conn.close()
            self.logger.info(f"✅ DB 연결 성공")
            return True
        except Exception as e:
            self.logger.error(f"❌ DB 연결 실패: {e}")
            return False 