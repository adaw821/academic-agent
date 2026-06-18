package org.example.academicagentbackend.controller;

import org.example.academicagentbackend.common.Result;
import org.example.academicagentbackend.model.ScheduleItemEntity;
import org.example.academicagentbackend.service.ScheduleService;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;
import org.springframework.web.multipart.MultipartFile;



//后端只返回数据 生成课表前端做
@RestController
//基础路径！！！一眼看到嗯
@RequestMapping("/api/schedule")

public class ScheduleController {

    //前端点击接口发送一张图片给后端
    //后端给llm解析后会返回json数据
    //然后前端把json包装成内置课表
    @PostMapping(value = "/send")
    public Result<ScheduleItemEntity> handleScheduleImage(MultipartFile file) {
        try {
            //处理文件为空的情况
            if(file == null){
                throw new IllegalArgumentException("文件不能为空");
            }
            //处理文件不是图片的情况
            String contentType = file.getContentType();
            if(contentType == null || contentType.equals("image/")) {
                throw new IllegalArgumentException("只可以上传图片");
            }
            //调用handleservice处理图片
            ScheduleService scheduleService = new ScheduleService();
            ScheduleItemEntity scheduleItemEntity = scheduleService.handleScheduleImage(file);

            //处理好之后返回被包装好的课程表实体
            return Result.success(scheduleItemEntity);

        } catch (Exception e) {
            return Result.error(500, e.getMessage());
        }

    }
}
