package org.example.academicagentbackend.controller;

import org.example.academicagentbackend.common.Result;
import org.example.academicagentbackend.model.ScheduleItemEntity;
import org.example.academicagentbackend.service.ScheduleService;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;
import org.springframework.web.multipart.MultipartFile;
import org.springframework.web.bind.annotation.RequestParam;
import java.util.List;


//后端只返回数据 生成课表前端做
@RestController
//基础路径！！！一眼看到嗯
@RequestMapping("/api/schedule")

public class ScheduleController {

    // 设定为final，这样创建一次即可
    private final ScheduleService scheduleService;

    public ScheduleController(ScheduleService scheduleService) {
        this.scheduleService = scheduleService;
    } 

    //前端点击接口发送一张图片给后端
    //后端给llm解析后会返回json数据
    //然后前端把json包装成内置课表
    @PostMapping(value = "/send")
    public Result<List<ScheduleItemEntity>> handleScheduleImage(@RequestParam("file") MultipartFile file) {
        try {
            //处理文件为空的情况
            if(file == null || file.isEmpty()){
                throw new IllegalArgumentException("文件不能为空");
            }
            //处理文件不是图片的情况
            String contentType = file.getContentType();
            if(contentType == null || !contentType.startsWith("image/")) {
                throw new IllegalArgumentException("只可以上传图片");
            }
            //调用注入进来的 service 处理图片(一张课表多门课,拿到的是 List)
            List<ScheduleItemEntity> courses = scheduleService.handleScheduleImage(file);

            //处理好之后返回课程列表
            return Result.success(courses);

        } catch (Exception e) {
            return Result.error(500, e.getMessage());
        }

    }
}
