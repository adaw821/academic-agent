package org.example.academicagentbackend.mapper;

import com.baomidou.mybatisplus.core.mapper.BaseMapper;
import org.apache.ibatis.annotations.Mapper;
import org.example.academicagentbackend.model.ScheduleItemEntity;

@Mapper
public interface ScheduleItemMapper extends BaseMapper<ScheduleItemEntity> {
    // 继承 BaseMapper 后，自动拥有 CRUD 方法
    // 不需要写任何代码！
}
