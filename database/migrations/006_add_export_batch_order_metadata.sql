USE [HlsKeyControl];
GO

ALTER TABLE dbo.ExportBatches
ADD
    customer_name NVARCHAR(160) NOT NULL
        CONSTRAINT DF_ExportBatches_CustomerName
        DEFAULT N'Не указано',

    order_number NVARCHAR(100) NOT NULL
        CONSTRAINT DF_ExportBatches_OrderNumber
        DEFAULT N'Не указано';
GO