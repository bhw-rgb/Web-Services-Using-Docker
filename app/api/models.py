# 데이터를 담는 테이블 정의
import uuid
import datetime
from sqlalchemy import String, Integer, DateTime, ForeignKey, Text, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass

# 1:M 관계 대응
class Conversation(Base):
    __tablename__ = "conversation"

    id: Mapped[str] = mapped_column(
        String(36), 
        primary_key=True, 
        default=uuid.uuid4,
    )
    created_at:Mapped[datetime.datetime] = mapped_column(
        DateTime, 
        server_default = func.now(),
    )

class Message(Base):
    __tablename__ = "message"

    id: Mapped[int] = mapped_column(
        Integer, 
        primary_key=True, 
        autoincrement=True,
    )
    conversation_id: Mapped[str] = mapped_column(
        String,
        ForeignKey("conversation.id")
    )
    role: Mapped[str] = mapped_column(String(10))       # user / assistant (llama에서 고정된 값)
    content: Mapped[str] = mapped_column(Text)
    created_at:Mapped[datetime.datetime] = mapped_column(
        DateTime, 
        server_default = func.now(),
    )