package org.example.academicagentbackend.service.impl;

import com.baomidou.mybatisplus.extension.service.IService;
import org.example.academicagentbackend.model.ScheduleItemEntity;
import org.springframework.web.multipart.MultipartFile;

public interface ScheduleServiceImpl extends IService<ScheduleItemEntity> {

    /**
     * 处理课程表图片并保存到数据库
     */
    ScheduleItemEntity handleScheduleImage(MultipartFile file);
}